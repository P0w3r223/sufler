"""Porty GitHub (Faza 3 / ADR 0020 read, ADR 0021 write) — kontrakt drzwi na GitHub REST.

``Protocol`` jak pozostałe porty — dowolna implementacja o zgodnych sygnaturach jest
akceptowana bez dziedziczenia. Read i write są ROZDZIELONE (jak ``NotesRepository`` vs
``NotesWriter``, ADR 0006): strona odczytu pozostaje jawnie read-only, a zdolność zapisu
wstrzykiwana jest tylko drzwiom z włączoną bramką (``enable_github_write``).

Metody są SYNCHRONICZNE (GitHub REST przez ``httpx.Client``): poller (async) woła je w puli
wątków — jak teams_graph odświeża sync MSAL — a narzędzia agenta wołają je wprost. Surowe
JSON (``list[dict]``) mapuje czysty ``selection`` w warstwie drzwi; treść z GitHuba to DANE.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from datetime import datetime


class GithubReadPort(Protocol):
    """Odczyt z GitHub (polling PAT): tożsamość konta + listy issue i komentarzy."""

    def authenticated_login(self) -> str:
        """Login konta PAT — do strażnika pętli self-ping (pomijamy własne zdarzenia)."""
        ...

    def list_issues(
        self, owner: str, repo: str, *, since: datetime | None = None, per_page: int = 50
    ) -> list[dict[str, Any]]:
        """Surowe issue od ``since`` (rosnąco), z paginacją; PR-y odfiltruje selection."""
        ...

    def list_issue_comments(
        self, owner: str, repo: str, *, since: datetime | None = None, per_page: int = 50
    ) -> list[dict[str, Any]]:
        """Surowe komentarze do issue zaktualizowane od ``since`` (rosnąco), z paginacją."""
        ...


class GithubWritePort(Protocol):
    """Zapis do GitHub (bramkowany, Gate 4 / ADR 0021) — CREATE-ONLY: nowe issue i komentarze."""

    def create_issue(
        self, owner: str, repo: str, title: str, body: str, labels: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """Utwórz nowe issue; zwróć surową odpowiedź GitHub (m.in. ``number``, ``html_url``)."""
        ...

    def create_comment(
        self, owner: str, repo: str, issue_number: int, body: str
    ) -> dict[str, Any]:
        """Dodaj komentarz do istniejącego issue; zwróć surową odpowiedź GitHub (``html_url``)."""
        ...
