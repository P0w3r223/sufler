"""Rozszerzony ODCZYT Jiry (ADR 0054, F+): szczegóły zgłoszenia, wyszukiwanie, zadania członka.

Uzupełnia ``MyJiraTasksService`` (samo „moje zadania") o trzy odczytowe zdolności bez mutacji.
Inaczej niż „moje zadania", te operacje BIORĄ parametry od wołającego (klucz, tekst, projekt) —
dlatego wartości sterowane przez wołającego są escapowane/whitelistowane w domenie (``jira_tasks``),
zanim trafią do JQL/URL. ``member_open_tasks`` bierze ``jira_user`` z ZAUFANEJ mapy tożsamości
(rozwiązanej w adapterze wejściowym), nie z surowego tekstu — nie da się nim wpisać cudzego konta.

Błędy transportu (401/403/429/timeout) tłumaczymy na ``JiraReadError`` na granicy, żeby narzędzie
zwróciło czytelny komunikat zamiast surowego ``httpx.HTTPError`` (jak w ``MyJiraTasksService``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import httpx

from workmate.core.application.my_jira_tasks import _MAX_HISTORY_RESULTS, _status_message
from workmate.core.domain.jira_tasks import (
    JiraTask,
    JiraTaskDetails,
    build_history_jql,
    build_my_tasks_jql,
    build_search_jql,
    map_my_tasks,
    map_task_details,
)
from workmate.core.errors import JiraReadError

if TYPE_CHECKING:
    from workmate.core.ports.jira import JiraReadPort

# Sufit wyników wyszukiwania — domykamy granicę tak jak przy zdarzeniach (model mógłby podać
# wielkie/ujemne ``limit``); po więcej idzie się węższym zapytaniem, nie jednym wielkim oknem.
_MAX_SEARCH_RESULTS = 20
_MAX_COMMENTS = 5


class JiraReadService:
    """Odczytowe zdolności Jiry ponad „moje zadania": szczegóły, wyszukiwanie, zadania członka."""

    def __init__(self, client: JiraReadPort, *, base_url: str = "") -> None:
        self._client = client
        self._base_url = base_url

    def task_details(self, key: str) -> JiraTaskDetails:
        """Szczegóły JEDNEGO zgłoszenia po kluczu + do 5 ostatnich komentarzy."""
        with _translated_errors():
            issue = self._client.get_issue(key)
            comments = self._client.list_comments(key, max_results=_MAX_COMMENTS)
        return map_task_details(issue, comments, base_url=self._base_url)

    def search_tasks(
        self,
        text: str = "",
        project: str = "",
        status_category: str = "",
        limit: int = _MAX_SEARCH_RESULTS,
    ) -> list[JiraTask]:
        """Wyszukaj zgłoszenia po tekście/projekcie/kategorii statusu (nierozwiązane, chyba że
        'done')."""
        jql = build_search_jql(text=text, project=project, status_category=status_category)
        capped = max(1, min(limit, _MAX_SEARCH_RESULTS))
        with _translated_errors():
            raw = self._client.search_issues(jql, max_results=capped, expand="")
        return map_my_tasks(raw[:capped], base_url=self._base_url)

    def member_open_tasks(self, jira_user: str) -> list[JiraTask]:
        """Otwarte zadania JEDNEGO członka zespołu — ``jira_user`` z zaufanej mapy tożsamości.

        Ta sama semantyka „moich zadań" (przypisane albo zgłoszone-nieprzypisane, nierozwiązane).
        """
        jql = build_my_tasks_jql(jira_user)
        with _translated_errors():
            raw = self._client.search_issues(jql, max_results=_MAX_SEARCH_RESULTS, expand="")
        return map_my_tasks(raw, base_url=self._base_url)

    def member_history(
        self, jira_user: str, since: str = "", until: str = ""
    ) -> tuple[list[JiraTask], bool]:
        """Zakończone zadania JEDNEGO członka w oknie dat — ``jira_user`` z zaufanej mapy
        tożsamości.

        (lista, czy_ucięto), najnowsze pierwsze — jak ``MyJiraTasksService.my_history``.
        """
        jql = build_history_jql(jira_user, since, until)
        with _translated_errors():
            raw = self._client.search_issues(jql, max_results=_MAX_HISTORY_RESULTS, expand="")
        tasks = map_my_tasks(raw, base_url=self._base_url)
        return tasks[:_MAX_HISTORY_RESULTS], len(tasks) > _MAX_HISTORY_RESULTS


class _translated_errors:
    """Kontekst tłumaczący błędy transportu httpx na ``JiraReadError`` (jak w „moich zadaniach")."""

    def __enter__(self) -> _translated_errors:
        return self

    def __exit__(self, exc_type: object, exc: BaseException | None, tb: object) -> Literal[False]:
        if exc is None:
            return False
        if isinstance(exc, httpx.HTTPStatusError):
            raise JiraReadError(_status_message(exc.response.status_code)) from exc
        if isinstance(exc, httpx.TimeoutException):
            raise JiraReadError(
                "Jira nie odpowiedziała w wyznaczonym czasie (timeout) — spróbuj ponownie."
            ) from exc
        if isinstance(exc, httpx.HTTPError):
            raise JiraReadError(f"nie udało się połączyć z Jirą: {exc}.") from exc
        return False
