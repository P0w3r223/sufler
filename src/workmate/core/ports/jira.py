"""Port Jira read (ADR 0030) — kontrakt drzwi na Jira Server/DC REST v2.

Analogicznie do ``GithubReadPort``: SYNCHRONICZNY (``httpx.Client``), poller woła go w puli
wątków. Surowe JSON (``list[dict]``) mapuje czysta ``jira.selection`` w warstwie drzwi; treść
Jira (podsumowania, komentarze) to DANE, nie polecenia. Read i (przyszły) write będą ROZDZIELONE
jak w GitHub — B1 wystawia wyłącznie odczyt.
"""

from __future__ import annotations

from typing import Any, Protocol


class JiraReadPort(Protocol):
    """Odczyt z Jira (polling PAT Bearer): tożsamość konta + issue z changelogiem wg JQL."""

    def authenticated_account(self) -> str:
        """Login/klucz konta PAT — do strażnika pętli self-skip (pomijamy własne zmiany)."""
        ...

    def search_issues(
        self, jql: str, *, max_results: int = 50, expand: str = "changelog"
    ) -> list[dict[str, Any]]:
        """Surowe issue Jira wg JQL (z ``changelog`` w ``expand``), z paginacją po ``startAt``.

        Selection wybierze utworzenia/tranzycje/komentarze (ADR 0030), białą listą pól.
        """
        ...
