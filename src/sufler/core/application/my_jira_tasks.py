"""Odczyt "moich zadań" Jira (ADR 0054) — jedna zdolność, zero mutacji, zero domysłów tożsamości.

``assignee`` jest USTALONY przy budowie serwisu (z konfiguracji albo z rozwiązanej tożsamości
nadawcy) i nigdy nie jest parametrem wywołania — to jedyna gwarancja, że narzędzie nie pokaże
cudzych zadań.

Błędy transportu (401/403/429/timeout) tłumaczy na ``JiraReadError`` ADAPTER
(``adapters/outbound/jira_http.as_jira_read_error``, wpięty w ``_get_json``/``_post_json`` obu
klientów). Ta warstwa nie zna ``httpx`` i nie ma czego łapać: z portu wychodzi już błąd domenowy
z gotowym komunikatem, a koperta narzędzia łapie go jako ``SuflerError``. Trzymanie tego
tłumaczenia tutaj wciągało ``httpx`` do heksagonu — czego ``lint-imports`` nie widzi, bo reguła
zabrania tylko importów z ``sufler.adapters``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sufler.core.domain.jira_tasks import (
    JiraTask,
    build_history_jql,
    build_my_tasks_jql,
    map_my_tasks,
)
from sufler.core.errors import InvalidRequestError

if TYPE_CHECKING:
    from sufler.core.ports.jira import JiraReadPort

# Sufit zadań otwartych. ``max_results`` przekazywane do portu jest rozmiarem STRONY, nie całości —
# adapter paginuje do dziesięciu stron, więc bez przycięcia po zmapowaniu do kontekstu modelu mogło
# wjechać pięćset zgłoszeń. Przycinamy i sygnalizujemy ``truncated``, jak ścieżki historii.
_MAX_RESULTS = 50
# Sufit historii — rok pracy może zwrócić więcej niż jedna strona; przycinamy i sygnalizujemy
# ``truncated``, żeby narzędzie kazało modelowi powiedzieć "pokazuję najnowsze 50" zamiast cicho
# gubić starsze wpisy.
_MAX_HISTORY_RESULTS = 50


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

    def my_open_tasks(self) -> tuple[list[JiraTask], bool]:
        """Otwarte zadania konta, po priorytecie i terminie; (lista, czy_ucięto) — maks. 50."""
        jql = build_my_tasks_jql(self._assignee)
        raw = self._client.search_issues(jql, max_results=_MAX_RESULTS, expand="")
        tasks = map_my_tasks(raw, base_url=self._base_url)
        return tasks[:_MAX_RESULTS], len(tasks) > _MAX_RESULTS

    def my_history(self, since: str = "", until: str = "") -> tuple[list[JiraTask], bool]:
        """Zakończone zadania konta w opcjonalnym oknie dat; (lista, czy_ucięto), najnowsze
        pierwsze."""
        jql = build_history_jql(self._assignee, since, until)
        raw = self._client.search_issues(jql, max_results=_MAX_HISTORY_RESULTS, expand="")
        tasks = map_my_tasks(raw, base_url=self._base_url)
        return tasks[:_MAX_HISTORY_RESULTS], len(tasks) > _MAX_HISTORY_RESULTS
