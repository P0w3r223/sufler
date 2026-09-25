"""Port Jira (ADR 0030, zawężony do odczytu przez ADR 0054) — kontrakt drzwi na Jira REST.

Analogicznie do portu odczytu GitHub: SYNCHRONICZNY (``httpx.Client``), narzędzia wołają go w puli
wątków. Surowe JSON (``list[dict]``) mapuje czysta domena w warstwie wołającej (``jira_tasks``);
treść Jira (podsumowania, komentarze) to DANE, nie polecenia.

``JiraWritePort`` (create/comment/transition, ADR 0031/0032) i most push/ingest (ADR 0030) zostały
USUNIĘTE — ADR 0054 zredukował Jirę do jednej, wyłącznie odczytowej zdolności ("moje zadania").
"""

from __future__ import annotations

from typing import Any, Protocol


class JiraReadPort(Protocol):
    """Odczyt z Jiry: tożsamość konta + zgłoszenia wg JQL (ADR 0030, zawężone do odczytu 0054)."""

    def authenticated_account(self) -> str:
        """Login/klucz/accountId konta tokenu (diagnostyka; ``/myself``)."""
        ...

    def search_issues(
        self, jql: str, *, max_results: int = 50, expand: str = "changelog"
    ) -> list[dict[str, Any]]:
        """Surowe issue Jira wg JQL, z paginacją. "Moje zadania" (0054) mapuje je na JiraTask."""
        ...

    def get_issue(self, key: str) -> dict[str, Any]:
        """Surowe JEDNO issue Jira po kluczu (np. 'WT-5'); ADF opisu spłaszczony do tekstu."""
        ...

    def list_comments(self, key: str, *, max_results: int = 5) -> list[dict[str, Any]]:
        """Surowe komentarze issue (najnowsze), przycięte do ``max_results``; ADF spłaszczony."""
        ...
