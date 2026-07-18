"""Bramkowany zapis do Jiry (Gate 5 / ADR 0031) — CREATE-ONLY: nowe zgłoszenia i komentarze.

Cienka orkiestracja nad portem ``JiraWritePort`` (bez I/O — testowalna na atrapie): sanityzacja
treści (dane od użytkownika Teams to DANE), twardy sufit długości, wyłącznie TWORZENIE (bez edycji,
usuwania i tranzycji — te wymagają osobnego ADR; tranzycja → ADR 0032). Po udanym zapisie zdarzenie
``source="teams"`` trafia do wspólnego magazynu (``EventStore``), żeby druga strona (poller/agent)
je zobaczyła — a że to ``source="teams"``, notifier (wypycha tylko ``source="jira"``) go zignoruje.

Projekt zapisu (``project``) pochodzi z KONFIGURACJI drzwi, nie z treści prośby. Komentarz ma
dodatkowy strażnik: klucz Jira zawiera projekt (``WM-5``), więc walidujemy, że prefiks == wskazany
projekt — inaczej niezaufana treść dopisałaby komentarz w CUDZYM projekcie (odpowiednik scope
owner/repo, który GitHub dostaje za darmo z URL-a).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from workmate.core.domain.events import NewEvent
from workmate.core.domain.sanitize import reject_dangerous_content
from workmate.core.errors import WriteError

if TYPE_CHECKING:
    from workmate.core.application.events import EventService
    from workmate.core.ports.jira import JiraWritePort

# Twarde sufity długości (obrona przed nadużyciem; Jira i tak ma własne limity pól).
_MAX_SUMMARY = 255  # limit pola summary w Jirze
_MAX_BODY = 30_000
_MAX_LABEL = 255
_MAX_TARGET = 255  # limit nazwy statusu/akcji docelowej tranzycji (ADR 0032)
# Pełny kształt klucza Jira (``PROJ-123``). Walidujemy CAŁY klucz, nie sam prefiks — inaczej
# ``WM-1/../OPS-1`` przeszedłby test projektu, a adapter (httpx normalizuje ``..``) dopisałby
# komentarz w CUDZYM projekcie. ``reject_dangerous_content`` nie pomoże (``/`` i ``.`` są ok).
_JIRA_KEY_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")

# Powody zatrzymania chodzenia po workflow (ADR 0032) — zwracane w raporcie, nigdy ciche.
_STOP_AMBIGUOUS = "ambiguous_target"  # >1 tranzycji pasuje do celu (model nie rozstrzygnie)
_STOP_BRANCH = "branch_point"  # cel niewidoczny, a stan ma rozgałęzienie (>1 opcji) — nie zgadujemy
_STOP_DEAD_END = "dead_end"  # cel niewidoczny i brak jakiejkolwiek tranzycji z bieżącego statusu
_STOP_CYCLE = "cycle"  # chodzenie wróciło do odwiedzonego statusu (pętla) — stop przed limitem
_STOP_HOP_CAP = "hop_cap"  # wyczerpano limit hopów (albo cap=1 i cel nie jest sąsiadem)
_STOP_ERROR = "error"  # błąd HTTP w trakcie hopa/odczytu — częściowy postęp zachowany w ``path``

# Sentinel: cel pasuje do >1 różnych tranzycji (nie da się wybrać bez zgadywania).
_AMBIGUOUS = object()


class JiraWriteService:
    """Tworzenie zgłoszeń/komentarzy w Jirze (create-only, bramkowane) z sanityzacją i echem."""

    def __init__(
        self,
        writer: JiraWritePort,
        *,
        project: str,
        issue_type: str = "Task",
        events: EventService | None = None,
        max_transition_hops: int = 1,
    ) -> None:
        self._writer = writer
        self._project = project.strip().upper()
        self._issue_type = issue_type
        self._events = events
        # Sufit hopów chodzenia po workflow (ADR 0032). 1 = zachowanie single-hop (bierze cel tylko
        # gdy jest bezpośrednim sąsiadem; nie przesuwa spekulacyjnie). >=2 włącza wielo-hop
        # (forced-advance). Konfigurację waliduje ``JiraSettings``; tu twarda podłoga zapasowa.
        self._max_hops = max(1, int(max_transition_hops))

    def create_issue(
        self, summary: str, description: str, labels: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """Utwórz nowe zgłoszenie w skonfigurowanym projekcie; zwróć ``{key, url}``. Create-only.

        Projekt bierzemy z konfiguracji (nie z treści) — prośba nie przekieruje zapisu indziej.
        """
        reject_dangerous_content(summary, description, *labels)
        summary = _bounded(summary.strip(), _MAX_SUMMARY, "podsumowanie zgłoszenia")
        description = _bounded(description.strip(), _MAX_BODY, "opis zgłoszenia")
        labels = tuple(_bounded(label.strip(), _MAX_LABEL, "etykieta") for label in labels)
        result = self._writer.create_issue(
            self._project, self._issue_type, summary, description, labels
        )
        self._echo_event(
            kind="jira_issue_created",
            external_id=str(result.get("key", "")),
            title=f"{result.get('key', '')}: {summary}".strip(": "),
            summary=description,
            result=result,
        )
        return {"key": result.get("key"), "url": result.get("url")}

    def create_comment(self, issue_key: str, body: str) -> dict[str, Any]:
        """Dodaj komentarz do zgłoszenia w skonfigurowanym projekcie; zwróć ``{url}``. Create-only.

        Strażnik: klucz musi należeć do skonfigurowanego projektu (prefiks przed ``-``) — inaczej
        ``WriteError`` (niezaufana treść nie dopisze w cudzym projekcie).
        """
        key = self._require_own_project(issue_key)
        reject_dangerous_content(body)
        body = _bounded(body.strip(), _MAX_BODY, "treść komentarza")
        result = self._writer.add_comment(key, body)
        self._echo_event(
            # Ten sam ``kind`` co odczyt (``jira_comment``) — echo i wpis czytany różni się źródłem
            # (``teams`` vs ``jira``), nie rodzajem; oba są w ``_KIND_LABELS`` (spójność renderu).
            # Dedup ``(source, external_id, kind)`` i tak trzyma echo i wpis czytany osobno.
            kind="jira_comment",
            external_id=str(result.get("id", "")),
            title=f"Komentarz do {key}",
            summary=body,
            result=result,
        )
        return {"url": result.get("url")}

    def transition_issue(self, issue_key: str, target_status: str) -> dict[str, Any]:
        """Przesuń zgłoszenie ku ``target_status`` best-effort (ADR 0032) — chodzenie greedy.

        Bierze bezpośredni hop, gdy cel jest widocznym sąsiadem (jeden POST). Gdy nie jest, a limit
        pozwala na wielo-hop (``max_hops>=2``), idzie DALEJ, ale tylko przez stany WYMUSZONE (jedna
        tranzycja) — na rozgałęzieniu STOP (nie zgadujemy, bo zgadywanie to nieodwracalna mutacja).
        Detekcja cyklu i limit hopów domykają blast radius. **Brak rollbacku**: każdy hop to trwała
        mutacja (post-functions Jiry). Zawsze zwraca STRUKTURALNY raport (``reached``/``path``/
        ``stop_reason``), nie gołe ``{"error"}`` — częściowy postęp jest widoczny dla wołającego.

        Wyjątek: błędy PRZED jakąkolwiek mutacją (zły klucz, niebezpieczna treść, nieudany pierwszy
        odczyt) lecą jako ``WriteError`` — koperta narzędzia zwróci ``{"error": ...}``.
        """
        key = self._require_own_project(issue_key)
        reject_dangerous_content(target_status)
        target = _bounded(target_status.strip(), _MAX_TARGET, "docelowy status")

        # Pierwszy odczyt jest pre-flight (przed mutacją): błąd HTTP → WriteError (koperta łapie).
        snap = self._writer.read_transitions(key)
        current = str(snap.get("current_status") or "")
        if current and _norm(current) == _norm(target):
            return _walk_report(True, current, [], None, ())  # już w celu — idempotentny no-op

        multi_hop = self._max_hops >= 2
        visited = {_norm(current)}
        path: list[dict[str, str]] = []
        for _ in range(self._max_hops):
            transitions = _as_transitions(snap.get("transitions"))
            match = _match_target(transitions, target)
            if match is _AMBIGUOUS:
                return _walk_report(False, current, path, _STOP_AMBIGUOUS, transitions)
            if isinstance(match, dict):
                # Cel jest widocznym sąsiadem → jeden POST i koniec (najczęstszy single-hop).
                try:
                    current = self._do_hop(key, current, match, path)
                except WriteError as exc:
                    return _walk_report(
                        False, current, path, _STOP_ERROR, transitions, detail=str(exc)
                    )
                return _walk_report(True, current, path, None, ())
            # Cel niewidoczny. Wielo-hop wyłączony (cap=1) → nie przesuwamy spekulacyjnie.
            if not multi_hop:
                return _walk_report(False, current, path, _STOP_HOP_CAP, transitions)
            forced = _sole_transition(transitions)
            if forced is None:
                reason = _STOP_BRANCH if transitions else _STOP_DEAD_END
                return _walk_report(False, current, path, reason, transitions)
            try:
                current = self._do_hop(key, current, forced, path)
            except WriteError as exc:
                # POST padł — issue został w bieżącym stanie, więc jego sąsiedzi (``transitions``)
                # są nadal aktualni jako ``available_next`` (model może ponowić z poprawnym celem).
                return _walk_report(False, current, path, _STOP_ERROR, transitions, detail=str(exc))
            try:
                snap = self._writer.read_transitions(key)
            except WriteError as exc:
                # Hop się udał, ale re-odczyt padł — issue jest już przesunięte, a nowych sąsiadów
                # nie znamy; puste ``available_next`` jest uczciwsze niż stare, nieaktualne.
                return _walk_report(False, current, path, _STOP_ERROR, (), detail=str(exc))
            current = str(snap.get("current_status") or current)
            if _norm(current) in visited:
                return _walk_report(False, current, path, _STOP_CYCLE, snap.get("transitions"))
            visited.add(_norm(current))
        # Wyczerpano limit hopów bez dojścia do celu (raport pokazuje ile faktycznie przeszliśmy).
        return _walk_report(False, current, path, _STOP_HOP_CAP, snap.get("transitions"))

    def _do_hop(
        self, key: str, from_status: str, transition: dict[str, str], path: list[dict[str, str]]
    ) -> str:
        """Wykonaj jeden hop (POST tranzycji), dopisz do ``path``, ostempluj echo; zwróć status.

        Może rzucić ``WriteError`` (błąd POST) — łapie go pętla i raportuje ``stop_reason='error'``
        z zachowanym ``path`` (poprzednie hopy już się zdarzyły — brak rollbacku).
        """
        result = self._writer.transition_issue(key, transition["id"])
        to_status = transition["to_status"] or str(result.get("status") or "")
        updated = str(result.get("updated") or "")
        path.append({"from": from_status, "to": to_status, "at": updated})
        self._echo_transition(key, from_status, to_status, updated, result)
        return to_status

    def _echo_transition(
        self, key: str, from_status: str, to_status: str, updated: str, result: dict[str, Any]
    ) -> None:
        """Zapisz echo ``source="teams"`` JEDNEGO hopa (best-effort) — druga strona je widzi.

        ``kind="jira_transition"`` (jak strona odczytu); ``external_id = f"{key}:{updated}"`` —
        unikat per hop dzięki ``updated`` (dwa hopy tego samego issue mają różne ``updated``, więc
        dedup ich nie skleja). Brak magazynu lub daty → pomijamy echo (tranzycja i tak się udała).
        """
        if self._events is None:
            return
        occurred_at = _parse_jira_ts(updated)
        if occurred_at is None:
            return
        self._events.ingest(
            NewEvent(
                source="teams",
                kind="jira_transition",
                external_id=f"{key}:{updated}",
                title=f"{key}: {to_status}",
                summary=f"{from_status}→{to_status}",
                url=str(result.get("url") or ""),
                occurred_at=occurred_at,
            )
        )

    def _require_own_project(self, issue_key: str) -> str:
        """Zwaliduj PEŁNY kształt klucza i wymuś zgodność projektu; inaczej ``WriteError``.

        Walidacja całego klucza (nie prefiksu) zamyka obejście path-traversal ``WM-1/../OPS-1``:
        taki „klucz" nie pasuje do ``PROJ-123``, więc odrzucamy go, zanim trafi do ścieżki REST.
        """
        key = issue_key.strip().upper()
        if not _JIRA_KEY_RE.fullmatch(key):
            raise WriteError(
                f"klucz odrzucony: {issue_key!r} nie jest poprawnym kluczem Jira (PROJ-123)."
            )
        prefix = key.split("-", 1)[0]
        if prefix != self._project:
            raise WriteError(
                f"klucz odrzucony: {issue_key!r} jest spoza skonfigurowanego "
                f"projektu {self._project!r}."
            )
        return key

    def _echo_event(
        self, *, kind: str, external_id: str, title: str, summary: str, result: dict[str, Any]
    ) -> None:
        """Zapisz zdarzenie ``source="teams"`` do magazynu (best-effort) — druga strona je widzi.

        ``occurred_at`` bierzemy ze znacznika Jiry z odpowiedzi (adapter go dostarcza), więc rdzeń
        nie woła zegara. Brak magazynu lub daty → pomijamy echo (zapis do Jiry i tak się udał).
        """
        if self._events is None:
            return
        occurred_at = _parse_jira_ts(result.get("created"))
        if occurred_at is None:
            return
        self._events.ingest(
            NewEvent(
                source="teams",
                kind=kind,
                external_id=external_id,
                title=title,
                summary=summary,
                url=str(result.get("url") or ""),
                occurred_at=occurred_at,
            )
        )


def _bounded(text: str, limit: int, label: str) -> str:
    """Zwróć ``text`` w granicy ``limit``; inaczej ``WriteError`` (nie tniemy po cichu)."""
    if len(text) > limit:
        raise WriteError(f"{label} przekracza limit {limit} znaków — zapis odrzucony.")
    return text


def _norm(text: Any) -> str:
    """Znormalizuj nazwę statusu/akcji: trim + casefold (dopasowanie bez wielkości liter)."""
    return str(text or "").strip().casefold()


def _as_transitions(raw: Any) -> list[dict[str, str]]:
    """Znormalizuj surowe tranzycje portu do ``[{id, name, to_status}]`` (odporne na ``None``)."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "to_status": str(item.get("to_status") or ""),
                }
            )
    return out


def _match_target(transitions: list[dict[str, str]], target: str) -> dict[str, str] | object | None:
    """Dopasuj cel: najpierw nazwa AKCJI, potem nazwa statusu docelowego (case-insensitive).

    Zwraca dopasowaną tranzycję, ``None`` gdy brak, albo sentinel ``_AMBIGUOUS`` gdy >1 różnych —
    akcja ma pierwszeństwo nad statusem (użytkownik nazwie przycisk workflow albo docelowy status).
    """
    t = _norm(target)
    by_action = [tr for tr in transitions if _norm(tr["name"]) == t]
    if len(by_action) == 1:
        return by_action[0]
    if len(by_action) > 1:
        return _AMBIGUOUS
    by_status = [tr for tr in transitions if _norm(tr["to_status"]) == t]
    if len(by_status) == 1:
        return by_status[0]
    if len(by_status) > 1:
        return _AMBIGUOUS
    return None


def _sole_transition(transitions: list[dict[str, str]]) -> dict[str, str] | None:
    """Zwróć jedyną tranzycję, gdy jest dokładnie jedna (stan WYMUSZONY); inaczej ``None``."""
    return transitions[0] if len(transitions) == 1 else None


def _walk_report(
    reached: bool,
    status: str,
    path: list[dict[str, str]],
    stop_reason: str | None,
    transitions: Any,
    *,
    detail: str | None = None,
) -> dict[str, Any]:
    """Zbuduj strukturalny raport chodzenia (ADR 0032) — nigdy gołe ``{"error"}``.

    ``available_next`` (nazwa akcji + status docelowy każdej widocznej tranzycji) to bezpieczne
    DANE: pozwala wołającemu wybrać kolejny ruch po zatrzymaniu. Przy sukcesie zostawiamy je puste
    (cel osiągnięty — kolejne kroki są bez znaczenia, oszczędzamy dodatkowy GET).
    """
    available = [
        {"action": tr["name"], "to_status": tr["to_status"]} for tr in _as_transitions(transitions)
    ]
    report: dict[str, Any] = {
        "transitioned": bool(path),
        "reached": reached,
        "status": status,
        "path": path,
        "hops": len(path),
        "stop_reason": stop_reason,
        "available_next": available,
    }
    if detail:
        report["detail"] = detail
    return report


def _parse_jira_ts(value: Any) -> datetime | None:
    """Znacznik Jiry (ISO z offsetem ``+0200``, milisekundy) → aware ``datetime``; zły → ``None``.

    Rdzeń nie importuje z adapterów, więc parsujemy tu (jak ``github._parse_iso``). ``%z`` obsługuje
    offset bez dwukropka; ``fromisoformat`` łapie warianty z dwukropkiem.
    """
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
