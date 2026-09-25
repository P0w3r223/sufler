"""Rozszerzony ODCZYT Jiry (ADR 0054, F+): szczegóły zgłoszenia, wyszukiwanie, zadania członka.

Uzupełnia ``MyJiraTasksService`` (samo „moje zadania") o trzy odczytowe zdolności bez mutacji.
Inaczej niż „moje zadania", te operacje BIORĄ parametry od wołającego (klucz, tekst, projekt) —
dlatego wartości sterowane przez wołającego są escapowane/whitelistowane w domenie (``jira_tasks``),
zanim trafią do JQL/URL. ``member_open_tasks`` bierze ``jira_user`` z ZAUFANEJ mapy tożsamości
(rozwiązanej w adapterze wejściowym), nie z surowego tekstu — nie da się nim wpisać cudzego konta.

Błędy transportu (401/403/429/timeout) tłumaczy ADAPTER, na swojej granicy
(``adapters/outbound/jira_http.as_jira_read_error``) — tak samo jak dla „moich zadań". Ta warstwa
nie zna ``httpx``: z portu wychodzi już ``JiraReadError`` z gotowym komunikatem.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sufler.core.application.my_jira_tasks import _MAX_HISTORY_RESULTS, _MAX_RESULTS
from sufler.core.domain.jira_tasks import (
    JiraTask,
    JiraTaskDetails,
    build_history_jql,
    build_my_tasks_jql,
    build_search_jql,
    map_my_tasks,
    map_task_details,
)

if TYPE_CHECKING:
    from sufler.core.ports.jira import JiraReadPort

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
        raw = self._client.search_issues(jql, max_results=capped, expand="")
        return map_my_tasks(raw[:capped], base_url=self._base_url)

    def member_open_tasks(self, jira_user: str) -> tuple[list[JiraTask], bool]:
        """Otwarte zadania JEDNEGO członka zespołu — ``jira_user`` z zaufanej mapy tożsamości.

        Ta sama semantyka „moich zadań" (przypisane albo zgłoszone-nieprzypisane, nierozwiązane),
        ten sam kształt wyniku ``(lista, czy_ucięto)`` i ten SAM sufit — ``_MAX_RESULTS`` z „moich
        zadań", nie ``_MAX_SEARCH_RESULTS``. Opis narzędzia obiecuje modelowi „to samo dla INNEJ
        osoby", więc dwa różne sufity znaczyłyby, że ktoś z 30 zadaniami widzi u siebie 30, a
        u kolegi 20 i ``truncated`` — różnica bez powodu, którego dałoby się bronić. Sufit
        wyszukiwania jest węższy rozmyślnie (``search`` przegląda całą Jirę) i tu nie pasuje.

        ``max_results`` jest rozmiarem STRONY, a adapter paginuje do dziesięciu stron, więc bez
        przycięcia po zmapowaniu wynik nie miał żadnego sufitu — sam parametr portu go nie daje.
        """
        jql = build_my_tasks_jql(jira_user)
        raw = self._client.search_issues(jql, max_results=_MAX_RESULTS, expand="")
        tasks = map_my_tasks(raw, base_url=self._base_url)
        return tasks[:_MAX_RESULTS], len(tasks) > _MAX_RESULTS

    def member_history(
        self, jira_user: str, since: str = "", until: str = ""
    ) -> tuple[list[JiraTask], bool]:
        """Zakończone zadania JEDNEGO członka w oknie dat — ``jira_user`` z zaufanej mapy
        tożsamości.

        (lista, czy_ucięto), najnowsze pierwsze — jak ``MyJiraTasksService.my_history``.
        """
        jql = build_history_jql(jira_user, since, until)
        raw = self._client.search_issues(jql, max_results=_MAX_HISTORY_RESULTS, expand="")
        tasks = map_my_tasks(raw, base_url=self._base_url)
        return tasks[:_MAX_HISTORY_RESULTS], len(tasks) > _MAX_HISTORY_RESULTS
