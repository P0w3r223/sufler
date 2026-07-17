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
# Pełny kształt klucza Jira (``PROJ-123``). Walidujemy CAŁY klucz, nie sam prefiks — inaczej
# ``WM-1/../OPS-1`` przeszedłby test projektu, a adapter (httpx normalizuje ``..``) dopisałby
# komentarz w CUDZYM projekcie. ``reject_dangerous_content`` nie pomoże (``/`` i ``.`` są ok).
_JIRA_KEY_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")


class JiraWriteService:
    """Tworzenie zgłoszeń/komentarzy w Jirze (create-only, bramkowane) z sanityzacją i echem."""

    def __init__(
        self,
        writer: JiraWritePort,
        *,
        project: str,
        issue_type: str = "Task",
        events: EventService | None = None,
    ) -> None:
        self._writer = writer
        self._project = project.strip().upper()
        self._issue_type = issue_type
        self._events = events

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

    def _require_own_project(self, issue_key: str) -> str:
        """Zwaliduj PEŁNY kształt klucza i wymuś zgodność projektu; inaczej ``WriteError``.

        Walidacja całego klucza (nie prefiksu) zamyka obejście path-traversal ``WM-1/../OPS-1``:
        taki „klucz" nie pasuje do ``PROJ-123``, więc odrzucamy go, zanim trafi do ścieżki REST.
        """
        key = issue_key.strip().upper()
        if not _JIRA_KEY_RE.fullmatch(key):
            raise WriteError(
                f"komentarz odrzucony: {issue_key!r} nie jest poprawnym kluczem Jira (PROJ-123)."
            )
        prefix = key.split("-", 1)[0]
        if prefix != self._project:
            raise WriteError(
                f"komentarz odrzucony: klucz {issue_key!r} jest spoza skonfigurowanego "
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
