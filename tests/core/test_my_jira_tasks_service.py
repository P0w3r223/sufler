"""Testy ``MyJiraTasksService`` (ADR 0054) — atrapa portu odczytu, żadnej sieci."""

from __future__ import annotations

from typing import Any

import pytest

from workmate.core.application.my_jira_tasks import MyJiraTasksService
from workmate.core.errors import InvalidRequestError, JiraReadError


class _FakeJiraRead:
    """Atrapa ``JiraReadPort`` — oddaje podane issue albo rzuca zadany błąd; zapamiętuje JQL."""

    def __init__(self, issues: list[dict[str, Any]] | None = None, error: Exception | None = None):
        self._issues = issues or []
        self._error = error
        self.jql_calls: list[str] = []

    def authenticated_account(self) -> str:
        return "bot"

    def search_issues(
        self, jql: str, *, max_results: int = 50, expand: str = "changelog"
    ) -> list[dict[str, Any]]:
        self.jql_calls.append(jql)
        if self._error is not None:
            raise self._error
        return self._issues


def test_requires_non_empty_assignee() -> None:
    with pytest.raises(InvalidRequestError):
        MyJiraTasksService(_FakeJiraRead(), assignee="")


def test_scopes_jql_to_configured_assignee() -> None:
    client = _FakeJiraRead(issues=[])
    service = MyJiraTasksService(client, assignee="mikolaj@example.org")
    service.my_open_tasks()
    assert client.jql_calls == [
        '(assignee = "mikolaj@example.org" OR (reporter = "mikolaj@example.org" AND assignee IS EMPTY)) '
        "AND resolution = EMPTY ORDER BY priority DESC, duedate ASC"
    ]


def test_maps_returned_issues() -> None:
    client = _FakeJiraRead(
        issues=[{"key": "WM-1", "fields": {"summary": "Coś", "status": {"name": "To Do"}}}]
    )
    service = MyJiraTasksService(client, assignee="mikolaj@example.org", base_url="https://jira.example.org")
    tasks, truncated = service.my_open_tasks()
    assert not truncated
    assert len(tasks) == 1
    assert tasks[0].key == "WM-1"
    assert tasks[0].url == "https://jira.example.org/browse/WM-1"


def test_empty_result_is_empty_list_not_error() -> None:
    service = MyJiraTasksService(_FakeJiraRead(issues=[]), assignee="mikolaj@example.org")
    assert service.my_open_tasks() == ([], False)


def test_max_results_is_a_PAGE_size_so_the_service_caps_the_whole_result() -> None:
    """``max_results`` przekazywane do portu jest rozmiarem STRONY, nie całości.

    Adapter paginuje do dziesięciu stron, więc bez przycięcia po zmapowaniu `my_tasks` mogło
    wsypać do kontekstu modelu pięćset zgłoszeń — dwie strony jednej odpowiedzi. Bliźniacza
    ścieżka historii tnie i sygnalizuje ``truncated``; ta nie robiła ani jednego, ani drugiego.
    """
    issues = [
        {"key": f"WM-{i}", "fields": {"summary": "x", "status": {"name": "To Do"}}}
        for i in range(120)
    ]
    service = MyJiraTasksService(_FakeJiraRead(issues=issues), assignee="mikolaj@example.org")

    tasks, truncated = service.my_open_tasks()

    assert len(tasks) == 50
    assert truncated is True


def test_a_result_that_fits_is_NOT_flagged_as_truncated() -> None:
    """Druga strona granicy — flaga ma znaczyć „coś ucięto", a nie „wynik był z Jiry"."""
    issues = [
        {"key": f"WM-{i}", "fields": {"summary": "x", "status": {"name": "To Do"}}}
        for i in range(50)
    ]
    service = MyJiraTasksService(_FakeJiraRead(issues=issues), assignee="mikolaj@example.org")

    tasks, truncated = service.my_open_tasks()

    assert len(tasks) == 50 and truncated is False


# --- Awaria odczytu: rdzeń PRZEPUSZCZA błąd portu, nie tłumaczy go i nie połyka ---
#
# Tłumaczenie httpx → ``JiraReadError`` przeniosło się na granicę adaptera
# (``adapters/outbound/jira_http.as_jira_read_error``), więc brzmienia komunikatów pilnują sondy
# adaptera (``tests/adapters/test_jira_api.py``). Tutaj zostaje to, co dalej należy do rdzenia:
# błąd portu ma dojść do wołającego NIETKNIĘTY. Sonda ma sens, bo obie metody robią po awarii coś
# jeszcze (mapowanie, przycięcie do sufitu) — połknięcie błędu dałoby PUSTĄ listę zadań, czyli
# „nie masz nic do zrobienia" zamiast „nie udało się zapytać".


@pytest.mark.parametrize("metoda", ["my_open_tasks", "my_history"])
def test_a_port_error_reaches_the_caller_untouched(metoda: str) -> None:
    awaria = JiraReadError("brak dostępu do Jiry — token jest nieważny albo bez uprawnień odczytu.")
    service = MyJiraTasksService(_FakeJiraRead(error=awaria), assignee="mikolaj@example.org")

    with pytest.raises(JiraReadError, match="brak dostępu") as exc:
        getattr(service, metoda)()

    assert exc.value is awaria, "rdzeń nie ma opakowywać błędu portu w drugi, własny"


# --- my_history (ADR 0059) ---------------------------------------------------


def test_my_history_scopes_jql_to_done_status() -> None:
    client = _FakeJiraRead(issues=[])
    service = MyJiraTasksService(client, assignee="mikolaj@example.org")
    service.my_history(since="2026-01-01")
    assert "statusCategory" in client.jql_calls[0]
    assert "resolved >=" in client.jql_calls[0]


def test_my_history_returns_tasks_and_not_truncated_under_the_cap() -> None:
    client = _FakeJiraRead(
        issues=[{"key": "WM-1", "fields": {"summary": "Zrobione", "status": {"name": "Done"}}}]
    )
    service = MyJiraTasksService(client, assignee="mikolaj@example.org")
    tasks, truncated = service.my_history()
    assert len(tasks) == 1
    assert truncated is False


def test_my_history_flags_truncation_beyond_the_cap() -> None:
    from workmate.core.application.my_jira_tasks import _MAX_HISTORY_RESULTS

    issues = [
        {"key": f"WM-{i}", "fields": {"summary": "x", "status": {"name": "Done"}}}
        for i in range(_MAX_HISTORY_RESULTS + 5)
    ]
    client = _FakeJiraRead(issues=issues)
    service = MyJiraTasksService(client, assignee="mikolaj@example.org")
    tasks, truncated = service.my_history()
    assert len(tasks) == _MAX_HISTORY_RESULTS
    assert truncated is True


def test_my_history_invalid_date_raises_invalid_request_error() -> None:
    service = MyJiraTasksService(_FakeJiraRead(issues=[]), assignee="mikolaj@example.org")
    with pytest.raises(InvalidRequestError):
        service.my_history(since="zła-data")
