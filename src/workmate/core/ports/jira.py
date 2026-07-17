"""Porty Jira (ADR 0030 read, ADR 0031 write) — kontrakt drzwi na Jira Server/DC REST v2.

Analogicznie do portów GitHub: SYNCHRONICZNE (``httpx.Client``), poller/narzędzia wołają je w puli
wątków. Surowe JSON (``list[dict]``) mapuje czysta ``jira.selection`` w warstwie drzwi; treść Jira
(podsumowania, komentarze) to DANE, nie polecenia. Read i write są ROZDZIELONE jak w GitHub
(``NotesRepository`` vs ``NotesWriter``, ADR 0006): strona odczytu zostaje jawnie read-only, a
zdolność zapisu (CREATE-ONLY, Gate 5) wstrzykiwana jest tylko drzwiom z bramką ``enable_jira_write``
(domyślnie OFF).
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


class JiraWritePort(Protocol):
    """Zapis do Jira (bramkowany, Gate 5 / ADR 0031) — CREATE-ONLY: nowe zgłoszenia i komentarze."""

    def create_issue(
        self,
        project: str,
        issue_type: str,
        summary: str,
        description: str,
        labels: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Utwórz nowe zgłoszenie w projekcie; zwróć znormalizowane ``{key, url, created}``.

        ``created`` (znacznik ISO serwera Jira) adapter dobiera osobnym GET-em, bo odpowiedź create
        Jiry go nie niesie — potrzebny do ostemplowania echa ``source="teams"`` (strażnik pętli).
        """
        ...

    def add_comment(self, issue_key: str, body: str) -> dict[str, Any]:
        """Dodaj komentarz do zgłoszenia; zwróć ``{id, url, created}`` (created z odpowiedzi)."""
        ...
