"""Czysta logika selekcji zdarzeń Jiry (bez I/O — pełna testowalność, jak github.selection).

Mapuje surowe issue z Jira REST v2 (``/search`` z ``expand=changelog``) na domenowe ``NewEvent``
trzech rodzajów: utworzenie zgłoszenia, zmiana statusu (tranzycja), komentarz. Egzekwuje strażnik
pętli SELF-SKIP (pomijamy zdarzenia autorstwa konta PAT) i buduje JQL z watermarkiem ``updated``.

Deduplikację po ``(source, external_id, kind)`` egzekwuje magazyn — tu jej nie powtarzamy. Klucze
dedup są STABILNE i NIEZMIENNE: klucz issue dla utworzenia, ``id`` wpisu changelogu dla tranzycji,
``id`` komentarza dla komentarza — dzięki temu kolejne aktualizacje tego samego issue (które JQL
zwraca wielokrotnie) nie mnożą zdarzeń, a każda zmiana statusu leci osobno (ADR 0030). Treść
(podsumowanie/opis/komentarz) to DANE, nie polecenia — nie interpretujemy jej.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from workmate.core.domain.events import NewEvent

_SOURCE = "jira"
# Sufit skrótu treści zdarzenia — opis/komentarz bywa długi, a zdarzenie ma być notką.
_MAX_SUMMARY = 500
# Znacznik nieparsowalnej daty — najstarszy możliwy, żeby nigdy nie „wygrał" watermarku.
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)
# Formaty znaczników Jira Server/DC: ISO 8601 z milisekundami i offsetem bez dwukropka
# (``2026-07-15T10:23:45.000+0200``); wariant bez milisekund jako zapas.
_JIRA_TS_FORMATS = ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z")


def build_jql(projects: Sequence[str], since: str = "") -> str:
    """Zbuduj JQL: nasłuchiwane projekty + opcjonalny watermark ``updated``, rosnąco po ``updated``.

    Klucze projektów Jira to ``[A-Z][A-Z0-9]+`` (bezpieczne bez cudzysłowów). ``since`` to watermark
    (ISO instant); do JQL formatujemy go MINUTOWO (``yyyy-MM-dd HH:mm`` — precyzja JQL). ``>=`` jest
    INKLUZYWNE, więc granicę re-pobieramy, a dedup magazynu chroni przed dublem. To tylko ZGRUBNY
    filtr serwerowy (koszt/backlog) — dokładną granicę egzekwuje ``select_events`` filtrem świeżości
    (Jira osadza komentarze/changelog w issue, więc ``updated`` bumpuje się przy KAŻDEJ zmianie pola
    i wciągnąłby całą historię starego tiketu bez filtra po ``occurred_at``).
    """
    keys = ", ".join(projects)
    jql = f"project in ({keys})"
    boundary = _parse(since) if since else _EPOCH
    if boundary != _EPOCH:
        jql += f' AND updated >= "{to_jql_datetime(boundary)}"'
    return jql + " ORDER BY updated ASC"


def to_jql_datetime(when: datetime) -> str:
    """Sformatuj chwilę do postaci akceptowanej przez JQL (``yyyy-MM-dd HH:mm``, minutowa precyzja).

    Dla aware ``datetime`` ``strftime`` używa JEGO WŁASNEGO zegara ściennego (bez konwersji strefy).
    Znaczniki Jiry niosą offset strefy zalogowanego użytkownika, a JQL interpretuje datę w TEJ SAMEJ
    strefie — więc formatowanie wprost jest strefowo spójne (bez arytmetyki stref). Patrz seed.
    """
    return when.strftime("%Y-%m-%d %H:%M")


def map_issue_created(
    raw: dict[str, Any], *, base_url: str = "", project: str = ""
) -> NewEvent | None:
    """Zmapuj surowe issue → ``NewEvent`` (kind ``jira_issue_created``); ``None`` bez klucza/daty.

    ``external_id`` = klucz issue (np. ``WM-1``): dedup emituje utworzenie RAZ (przy pierwszym
    zobaczeniu), a późniejsze aktualizacje tego issue są pomijane. Autor = ``creator`` (zapas
    ``reporter``).
    """
    key = str(raw.get("key") or "")
    fields = _fields(raw)
    created = fields.get("created")
    if not key or not created:
        return None
    description = fields.get("description")
    return NewEvent(
        source=_SOURCE,
        kind="jira_issue_created",
        external_id=key,
        actor=_author(fields.get("creator") or fields.get("reporter")),
        title=f"{key}: {str(fields.get('summary') or '')}".rstrip(": "),
        summary=_clip(description) if isinstance(description, str) else "",
        url=_browse_url(base_url, key),
        project=project,
        occurred_at=_parse(str(created)),
    )


def map_transitions(
    raw: dict[str, Any], *, base_url: str = "", project: str = ""
) -> list[NewEvent]:
    """Zmapuj wpisy changelogu ze zmianą pola ``status`` → ``NewEvent`` (``jira_transition``).

    Jeden wpis changelogu = jedno zdarzenie; ``external_id`` = ``id`` wpisu (stabilne, niezmienne),
    więc każda zmiana statusu leci osobno, a klucz issue by je skleił w dedupie. Wpisy bez zmiany
    statusu (przypisanie, etykieta, opis) pomijamy — to szum. Autor = autor wpisu changelogu.
    """
    key = str(raw.get("key") or "")
    if not key:
        return []
    histories = _histories(raw)
    events: list[NewEvent] = []
    for history in histories:
        transition = _status_change(history.get("items"))
        history_id = history.get("id")
        created = history.get("created")
        if transition is None or history_id is None or not created:
            continue
        frm, to = transition
        summary = f"{frm} → {to}" if frm else to
        events.append(
            NewEvent(
                source=_SOURCE,
                kind="jira_transition",
                external_id=str(history_id),
                actor=_author(history.get("author")),
                title=f"{key}: {to}",
                summary=summary,
                url=_browse_url(base_url, key),
                project=project,
                occurred_at=_parse(str(created)),
            )
        )
    return events


def map_comments(raw: dict[str, Any], *, base_url: str = "", project: str = "") -> list[NewEvent]:
    """Zmapuj komentarze z ``fields.comment.comments`` → ``NewEvent`` (``jira_comment``).

    ``external_id`` = ``id`` komentarza (unikalne), więc każdy komentarz to osobne zdarzenie; klucz
    issue by je skleił. Autor = autor komentarza. URL prowadzi do komentarza (``focusedCommentId``).
    """
    key = str(raw.get("key") or "")
    if not key:
        return []
    comments = _comments(raw)
    events: list[NewEvent] = []
    for comment in comments:
        comment_id = comment.get("id")
        created = comment.get("created")
        if comment_id is None or not created:
            continue
        body = comment.get("body")
        events.append(
            NewEvent(
                source=_SOURCE,
                kind="jira_comment",
                external_id=str(comment_id),
                actor=_author(comment.get("author")),
                title=f"Komentarz do {key}",
                summary=_clip(body) if isinstance(body, str) else "",
                url=_browse_url(base_url, key, comment_id=str(comment_id)),
                project=project,
                occurred_at=_parse(str(created)),
            )
        )
    return events


def select_events(
    raw_issues: Sequence[dict[str, Any]],
    *,
    self_account: str,
    base_url: str = "",
    project_map: dict[str, str] | None = None,
    since: str = "",
) -> list[NewEvent]:
    """Zmapuj issue na zdarzenia trzech rodzajów, egzekwuj self-skip i świeżość, posortuj po czasie.

    Każde issue rodzi utworzenie + tranzycje (ze zmianą statusu) + komentarze. ``project_map``
    (klucz projektu Jira → klucz projektu WorkMate, ADR 0028) rozstrzyga ``event.project`` PER ISSUE
    — bo jedne drzwi mogą nasłuchiwać wielu projektów mapujących na różne projekty WorkMate. Brak
    dopasowania → puste ``project`` (best-effort, jak GitHub). Self-skip stosujemy PER ZDARZENIE po
    jego autorze: zmiana statusu/komentarz OD innej osoby na naszym issue nadal leci.

    FILTR ŚWIEŻOŚCI (``since`` = poprzedni watermark, instant): Jira osadza komentarze i changelog
    w issue, a ``updated`` bumpuje się przy KAŻDEJ zmianie pola — więc dotknięty po starcie stary
    tiket JQL wciąga z CAŁĄ historią. Bez filtra wypchnęlibyśmy stare utworzenie i archiwalne
    komentarze/tranzycje. Przepuszczamy więc tylko zdarzenia ``occurred_at >=`` granicy (INKLUZYWNIE
    — dedup domyka dubel na granicy). Filtr KLIENCKI i WYKLUCZAJĄCY, lecz bezpieczny: ``updated``
    issue jest zawsze >= czasu każdego jego sub-zdarzenia, więc watermark (max ``updated``) nie
    przeskoczy nieemitowanego świeżego zdarzenia (jak CI/recenzje GitHuba). Pusty ``since`` (przed
    seedem) wyłącza filtr — nie gubimy zdarzeń.
    """
    boundary = _boundary(since)
    events: list[NewEvent] = []
    for raw in raw_issues:
        project = (project_map or {}).get(_project_key(raw), "")
        created = map_issue_created(raw, base_url=base_url, project=project)
        candidates: list[NewEvent] = [created] if created is not None else []
        candidates.extend(map_transitions(raw, base_url=base_url, project=project))
        candidates.extend(map_comments(raw, base_url=base_url, project=project))
        events.extend(
            ev
            for ev in candidates
            if _from_other_actor(ev, self_account) and _is_fresh(ev, boundary)
        )
    return sorted(events, key=lambda e: e.occurred_at)


def next_since(raw_issues: Sequence[dict[str, Any]], current: str = "") -> str:
    """Nowy watermark = najnowszy ``fields.updated`` w partii jako ISO instant (albo dotychczasowy).

    Zwracamy PEŁNY aware ISO (nie minutowy JQL): to zarazem granica klienckiego filtra świeżości
    (``select_events``), więc musi zachować sekundy i strefę. Do JQL degraduje go ``build_jql``.
    Porównujemy INSTANTY (aware ``datetime``) — poprawne nawet przy różnych offsetach.
    """
    newest: datetime | None = None
    for raw in raw_issues:
        updated = _fields(raw).get("updated")
        if not updated:
            continue
        parsed = _parse(str(updated))
        if parsed == _EPOCH:
            continue
        if newest is None or parsed > newest:
            newest = parsed
    return newest.isoformat() if newest is not None else current


def _from_other_actor(event: NewEvent, self_account: str) -> bool:
    """Czy zdarzenie NIE pochodzi od konta bota (PAT) — strażnik pętli self-skip.

    Puste ``self_account`` (nieustalone) wyłącza filtr, żeby nie zgubić wszystkich zdarzeń.
    """
    return not self_account or event.actor != self_account


def _is_fresh(event: NewEvent, boundary: datetime | None) -> bool:
    """Czy zdarzenie jest na/po granicy watermarku (INKLUZYWNIE); brak granicy → wszystko."""
    return boundary is None or event.occurred_at >= boundary


def _boundary(since: str) -> datetime | None:
    """Granica filtra świeżości z watermarku ISO; ``None`` (bez filtra), gdy pusty/zły/naiwny.

    Naiwny znacznik (legacy/ręczny stan bez strefy) świadomie wyłącza filtr zamiast ryzykować
    ``TypeError`` przy porównaniu z aware ``occurred_at`` — watermark piszemy zawsze jako aware ISO.
    """
    if not since:
        return None
    parsed = _parse(since)
    if parsed == _EPOCH or parsed.tzinfo is None:
        return None
    return parsed


def _fields(raw: dict[str, Any]) -> dict[str, Any]:
    fields = raw.get("fields")
    return fields if isinstance(fields, dict) else {}


def _histories(raw: dict[str, Any]) -> list[dict[str, Any]]:
    changelog = raw.get("changelog")
    histories = changelog.get("histories") if isinstance(changelog, dict) else None
    return [h for h in histories if isinstance(h, dict)] if isinstance(histories, list) else []


def _comments(raw: dict[str, Any]) -> list[dict[str, Any]]:
    comment = _fields(raw).get("comment")
    items = comment.get("comments") if isinstance(comment, dict) else None
    return [c for c in items if isinstance(c, dict)] if isinstance(items, list) else []


def _status_change(items: Any) -> tuple[str, str] | None:
    """Z listy ``items`` wpisu changelogu wyłuskaj zmianę pola ``status`` jako ``(z, do)``.

    Server używa klucza ``field``, nowsze API bywa ``fieldId`` — akceptujemy oba. ``None``, gdy
    wpis nie zmienia statusu (przypisanie/etykieta/opis — szum, nie tranzycja).
    """
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("field") == "status" or item.get("fieldId") == "status":
            return str(item.get("fromString") or ""), str(item.get("toString") or "")
    return None


def _project_key(raw: dict[str, Any]) -> str:
    """Klucz projektu Jira: ``fields.project.key`` (zapas: prefiks klucza issue przed ``-``)."""
    project = _fields(raw).get("project")
    if isinstance(project, dict) and project.get("key"):
        return str(project["key"]).upper()
    key = str(raw.get("key") or "")
    return key.split("-", 1)[0].upper() if "-" in key else ""


def _author(user: Any) -> str:
    """Login autora z obiektu użytkownika Jira: ``name``/``key`` (Server) lub ``accountId`` (Cloud).

    Spójne z ``HttpxJiraClient.authenticated_account`` — self-skip porównuje te same pola.
    """
    if not isinstance(user, dict):
        return ""
    return str(user.get("name") or user.get("key") or user.get("accountId") or "")


def _browse_url(base_url: str, key: str, *, comment_id: str = "") -> str:
    """Adres przeglądarki issue (``{base}/browse/{key}``), opcjonalnie skupiony na komentarzu."""
    if not base_url or not key:
        return ""
    url = f"{base_url.rstrip('/')}/browse/{key}"
    return f"{url}?focusedCommentId={comment_id}" if comment_id else url


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _MAX_SUMMARY else text[:_MAX_SUMMARY] + " […]"


def _parse(value: str) -> datetime:
    """Znacznik Jiry (ISO z offsetem, milisekundy) → aware ``datetime``; niepoprawny → epoka.

    Epoka (zamiast wyjątku) jest bezpieczna: przy sortowaniu ląduje na początku, a przy watermarku
    jawnie ją pomijamy (patrz ``next_since``). Znaki ``+0200`` bez dwukropka obsługuje ``%z``.
    """
    value = value.strip()
    if not value:
        return _EPOCH
    for fmt in _JIRA_TS_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return _EPOCH
