"""Testy ``JiraReadService`` (ADR 0059, rozszerzony odczyt Jiry) — atrapa portu, żadnej sieci."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from workmate.core.application.jira_read import JiraReadService
from workmate.core.errors import JiraReadError


class _FakeJiraRead:
    """Atrapa ``JiraReadPort`` rozszerzonego odczytu — zapamiętuje wywołania, oddaje kanned dane."""

    def __init__(
        self,
        issues: list[dict[str, Any]] | None = None,
        issue: dict[str, Any] | None = None,
        comments: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._issues = issues or []
        self._issue = issue or {}
        self._comments = comments or []
        self._error = error
        self.jql_calls: list[str] = []
        self.max_results_calls: list[int] = []
        self.get_issue_calls: list[str] = []

    def authenticated_account(self) -> str:
        return "bot"

    def search_issues(
        self, jql: str, *, max_results: int = 50, expand: str = "changelog"
    ) -> list[dict[str, Any]]:
        self.jql_calls.append(jql)
        self.max_results_calls.append(max_results)
        if self._error is not None:
            raise self._error
        return self._issues

    def get_issue(self, key: str) -> dict[str, Any]:
        self.get_issue_calls.append(key)
        if self._error is not None:
            raise self._error
        return self._issue

    def list_comments(self, key: str, *, max_results: int = 5) -> list[dict[str, Any]]:
        if self._error is not None:
            raise self._error
        return self._comments


# --- task_details ----------------------------------------------------------------


def test_task_details_maps_issue_and_comments() -> None:
    client = _FakeJiraRead(
        issue={"key": "WT-5", "fields": {"summary": "Task"}},
        comments=[{"author": {"displayName": "Adam"}, "body": "ping"}],
    )
    service = JiraReadService(client, base_url="https://x.atlassian.net")
    details = service.task_details("WT-5")
    assert details.key == "WT-5"
    assert details.url == "https://x.atlassian.net/browse/WT-5"
    assert details.comments[0].author == "Adam"
    assert client.get_issue_calls == ["WT-5"]


def test_task_details_translates_auth_error() -> None:
    response = httpx.Response(403, request=httpx.Request("GET", "https://jira.example"))
    error = httpx.HTTPStatusError("boom", request=response.request, response=response)
    service = JiraReadService(_FakeJiraRead(error=error))
    with pytest.raises(JiraReadError, match="brak dostępu"):
        service.task_details("WT-5")


# --- search_tasks ------------------------------------------------------------------


def test_search_tasks_uses_search_jql_and_maps_results() -> None:
    client = _FakeJiraRead(issues=[{"key": "WT-1", "fields": {"summary": "A"}}])
    service = JiraReadService(client)
    tasks = service.search_tasks(text="scada")
    assert [t.key for t in tasks] == ["WT-1"]
    assert 'text ~ "scada"' in client.jql_calls[0]


def test_search_tasks_caps_limit_at_max() -> None:
    from workmate.core.application.jira_read import _MAX_SEARCH_RESULTS

    client = _FakeJiraRead(issues=[])
    service = JiraReadService(client)
    service.search_tasks(text="x", limit=10_000)
    assert client.max_results_calls == [_MAX_SEARCH_RESULTS]


# --- member_open_tasks / member_history (jira_user z zaufanej mapy) ---------------


def test_member_open_tasks_scopes_jql_to_given_account() -> None:
    client = _FakeJiraRead(issues=[])
    service = JiraReadService(client)
    service.member_open_tasks("kolega@example.org")
    assert "kolega@example.org" in client.jql_calls[0]


def test_member_history_scopes_jql_and_reports_truncation() -> None:
    from workmate.core.application.my_jira_tasks import _MAX_HISTORY_RESULTS

    issues = [
        {"key": f"WT-{i}", "fields": {"summary": "x"}} for i in range(_MAX_HISTORY_RESULTS + 3)
    ]
    client = _FakeJiraRead(issues=issues)
    service = JiraReadService(client)
    tasks, truncated = service.member_history("kolega@example.org")
    assert len(tasks) == _MAX_HISTORY_RESULTS
    assert truncated is True
    assert "kolega@example.org" in client.jql_calls[0]


def test_member_history_translates_timeout_error() -> None:
    error = httpx.ConnectTimeout("timed out")
    service = JiraReadService(_FakeJiraRead(error=error))
    with pytest.raises(JiraReadError, match="timeout"):
        service.member_history("kolega@example.org")
