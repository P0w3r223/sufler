"""Bramkowany zapis do GitHub (Gate 4 / ADR 0021) — CREATE-ONLY: nowe issue i komentarze.

Cienka orkiestracja nad portem ``GithubWritePort`` (bez I/O — testowalna na atrapie): sanityzacja
treści (dane od użytkownika Teams to DANE), twardy sufit długości, wyłącznie TWORZENIE (bez edycji
i usuwania — te wymagałyby osobnego ADR). Po udanym zapisie zdarzenie ``source="teams"`` trafia do
wspólnego magazynu (``EventStore``), żeby druga strona (GitHub/agent) je „zobaczyła" — jako
``source="teams"``, więc notifier (wypycha tylko ``source="github"``) NIE odeśle go z powrotem.

Miejsce zapisu (``owner``/``repo``) pochodzi z KONFIGURACJI drzwi, nie z treści prośby — treść nie
może przekierować zapisu do cudzego repozytorium.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from workmate.core.domain.events import NewEvent
from workmate.core.domain.sanitize import reject_dangerous_content
from workmate.core.errors import WriteError

if TYPE_CHECKING:
    from workmate.core.application.events import EventService
    from workmate.core.ports.github import GithubWritePort

# Twarde sufity długości (obrona przed nadużyciem; GitHub i tak ma własne limity ~65k treści).
_MAX_TITLE = 256
_MAX_BODY = 60_000


class GithubWriteService:
    """Tworzenie issue/komentarzy w GitHub (create-only, bramkowane) z sanityzacją i echem."""

    def __init__(
        self,
        writer: GithubWritePort,
        *,
        owner: str,
        repo: str,
        events: EventService | None = None,
    ) -> None:
        self._writer = writer
        self._owner = owner
        self._repo = repo
        self._events = events

    def create_issue(
        self, title: str, body: str, labels: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """Utwórz nowe issue; zwróć ``{number, url}``. Sanityzacja + sufit długości, create-only."""
        reject_dangerous_content(title, body, *labels)
        title = _bounded(title.strip(), _MAX_TITLE, "tytuł issue")
        body = _bounded(body.strip(), _MAX_BODY, "treść issue")
        result = self._writer.create_issue(self._owner, self._repo, title, body, labels)
        self._echo_event(
            kind="github_issue_created",
            external_id=str(result.get("number", "")),
            title=title,
            summary=body,
            result=result,
        )
        return {"number": result.get("number"), "url": result.get("html_url")}

    def create_comment(self, issue_number: int, body: str) -> dict[str, Any]:
        """Dodaj komentarz do issue; zwróć ``{url}``. Sanityzacja + sufit długości, create-only."""
        reject_dangerous_content(body)
        body = _bounded(body.strip(), _MAX_BODY, "treść komentarza")
        result = self._writer.create_comment(self._owner, self._repo, issue_number, body)
        self._echo_event(
            kind="github_comment_created",
            external_id=str(result.get("id", "")),
            title=f"Komentarz do issue #{issue_number}",
            summary=body,
            result=result,
        )
        return {"url": result.get("html_url")}

    def _echo_event(
        self, *, kind: str, external_id: str, title: str, summary: str, result: dict[str, Any]
    ) -> None:
        """Zapisz zdarzenie ``source="teams"`` do magazynu (best-effort) — druga strona je widzi.

        ``occurred_at`` bierzemy z odpowiedzi GitHub (``created_at`` — czas serwera), więc rdzeń nie
        woła zegara. Brak magazynu lub daty → pomijamy echo (zapis do GitHub i tak się udał).
        """
        if self._events is None:
            return
        occurred_at = _parse_iso(result.get("created_at"))
        if occurred_at is None:
            return
        self._events.ingest(
            NewEvent(
                source="teams",
                kind=kind,
                external_id=external_id,
                title=title,
                summary=summary,
                url=str(result.get("html_url") or ""),
                occurred_at=occurred_at,
            )
        )


def _bounded(text: str, limit: int, label: str) -> str:
    """Zwróć ``text`` w granicy ``limit``; inaczej ``WriteError`` (nie tniemy po cichu)."""
    if len(text) > limit:
        raise WriteError(f"{label} przekracza limit {limit} znaków — zapis odrzucony.")
    return text


def _parse_iso(value: Any) -> datetime | None:
    """ISO-8601 z GitHuba (``…Z``) → aware UTC; puste/niepoprawne → ``None`` (pomiń echo)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
