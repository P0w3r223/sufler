"""Testy ``MyJiraTasksService`` (ADR 0054) — atrapa portu odczytu, żadnej sieci."""

from __future__ import annotations

from typing import Any

import httpx
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
        'assignee = "mikolaj@example.org" AND resolution = EMPTY ORDER BY priority DESC, duedate ASC'
    ]


def test_maps_returned_issues() -> None:
    client = _FakeJiraRead(
        issues=[{"key": "WM-1", "fields": {"summary": "Coś", "status": {"name": "To Do"}}}]
    )
    service = MyJiraTasksService(client, assignee="mikolaj@example.org", base_url="https://jira.example.org")
    tasks = service.my_open_tasks()
    assert len(tasks) == 1
    assert tasks[0].key == "WM-1"
    assert tasks[0].url == "https://jira.example.org/browse/WM-1"


def test_empty_result_is_empty_list_not_error() -> None:
    service = MyJiraTasksService(_FakeJiraRead(issues=[]), assignee="mikolaj@example.org")
    assert service.my_open_tasks() == []


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_error_raises_readable_jira_read_error(status_code: int) -> None:
    response = httpx.Response(status_code, request=httpx.Request("GET", "https://jira.example"))
    error = httpx.HTTPStatusError("boom", request=response.request, response=response)
    service = MyJiraTasksService(_FakeJiraRead(error=error), assignee="mikolaj@example.org")
    with pytest.raises(JiraReadError, match="brak dostępu"):
        service.my_open_tasks()


def test_rate_limit_error_raises_readable_jira_read_error() -> None:
    response = httpx.Response(429, request=httpx.Request("GET", "https://jira.example"))
    error = httpx.HTTPStatusError("boom", request=response.request, response=response)
    service = MyJiraTasksService(_FakeJiraRead(error=error), assignee="mikolaj@example.org")
    with pytest.raises(JiraReadError, match="429"):
        service.my_open_tasks()


def test_timeout_raises_readable_jira_read_error() -> None:
    error = httpx.ConnectTimeout("timed out")
    service = MyJiraTasksService(_FakeJiraRead(error=error), assignee="mikolaj@example.org")
    with pytest.raises(JiraReadError, match="timeout"):
        service.my_open_tasks()


def test_generic_transport_error_raises_readable_jira_read_error() -> None:
    error = httpx.ConnectError("dns failed")
    service = MyJiraTasksService(_FakeJiraRead(error=error), assignee="mikolaj@example.org")
    with pytest.raises(JiraReadError, match="nie udało się połączyć"):
        service.my_open_tasks()
