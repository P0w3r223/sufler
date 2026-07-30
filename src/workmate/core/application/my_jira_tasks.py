"""Odczyt "moich zadań" Jira (ADR 0054) — jedna zdolność, zero mutacji, zero domysłów tożsamości.

``assignee`` jest USTALONY przy budowie serwisu (z konfiguracji albo z rozwiązanej tożsamości
nadawcy) i nigdy nie jest parametrem wywołania — to jedyna gwarancja, że narzędzie nie pokaże
cudzych zadań. Błędy transportu (401/403/429/timeout) tłumaczymy na ``JiraReadError`` na granicy
adaptera, żeby wołający dostał czytelny komunikat zamiast surowego ``httpx.HTTPError``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from workmate.core.domain.jira_tasks import JiraTask, build_my_tasks_jql, map_my_tasks
from workmate.core.errors import InvalidRequestError, JiraReadError

if TYPE_CHECKING:
    from workmate.core.ports.jira import JiraReadPort

_MAX_RESULTS = 50


class MyJiraTasksService:
    """Zwraca zadania Jiry przypisane do JEDNEGO, z góry ustalonego konta."""

    def __init__(self, client: JiraReadPort, *, assignee: str, base_url: str = "") -> None:
        if not assignee:
            raise InvalidRequestError(
                "brak konta Jira do zapytania — tożsamość pytającego nie jest rozwiązana "
                "(fail-closed, ADR 0054)."
            )
        self._client = client
        self._assignee = assignee
        self._base_url = base_url

    def my_open_tasks(self) -> list[JiraTask]:
        """Otwarte zadania przypisane do skonfigurowanego konta, po priorytecie i terminie."""
        jql = build_my_tasks_jql(self._assignee)
        try:
            raw = self._client.search_issues(jql, max_results=_MAX_RESULTS, expand="")
        except httpx.HTTPStatusError as exc:
            raise JiraReadError(_status_message(exc.response.status_code)) from exc
        except httpx.TimeoutException as exc:
            raise JiraReadError(
                "Jira nie odpowiedziała w wyznaczonym czasie (timeout) — spróbuj ponownie."
            ) from exc
        except httpx.HTTPError as exc:
            raise JiraReadError(f"nie udało się połączyć z Jirą: {exc}.") from exc
        return map_my_tasks(raw, base_url=self._base_url)


def _status_message(status_code: int) -> str:
    if status_code in (401, 403):
        return "brak dostępu do Jiry — token jest nieważny albo bez uprawnień odczytu."
    if status_code == 429:
        return "Jira ogranicza liczbę żądań (429) — spróbuj ponownie za chwilę."
    return f"Jira odpowiedziała błędem (HTTP {status_code})."
