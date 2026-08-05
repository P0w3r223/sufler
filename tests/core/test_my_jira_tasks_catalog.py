"""Testy katalogu narzędzi "moje zadania"/"moja historia" Jira (ADR 0054/0059).

Katalog jest cienki, ale niesie inwarianty warte przypięcia: brak parametru tożsamości (nie da
się nim podejrzeć cudzych zadań), koperta zamienia błędy na ``{"error": ...}``, "moje zadania"
rozdziela przypisane/nieprzypisane, "moja historia" niesie flagę ``truncated``.
"""

from __future__ import annotations

import inspect

from workmate.core.application.tools import build_my_jira_tasks_catalog
from workmate.core.domain.jira_tasks import JiraTask
from workmate.core.errors import JiraReadError


class _FakeMyJiraTasksService:
    def __init__(
        self,
        tasks: list[JiraTask] | None = None,
        history: tuple[list[JiraTask], bool] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._tasks = tasks or []
        self._history = history or ([], False)
        self._error = error
        self.calls = 0
        self.history_calls: list[tuple[str, str]] = []

    def my_open_tasks(self) -> list[JiraTask]:
        self.calls += 1
        if self._error:
            raise self._error
        return self._tasks

    def my_history(self, since: str = "", until: str = "") -> tuple[list[JiraTask], bool]:
        self.history_calls.append((since, until))
        if self._error:
            raise self._error
        return self._history


def _catalog(**kwargs):
    service = _FakeMyJiraTasksService(**kwargs)
    return service, {spec.name: spec for spec in build_my_jira_tasks_catalog(service)}  # type: ignore[arg-type]


def test_catalog_exposes_tasks_and_history_tools() -> None:
    _, specs = _catalog()
    assert set(specs) == {"get_my_jira_tasks", "get_my_jira_history"}


def test_get_my_jira_tasks_takes_no_identity_parameter() -> None:
    """Zero parametrów de-tożsamość jest kontraktem bezpieczeństwa: nie da się poprosić o cudze."""
    _, specs = _catalog()
    signature = inspect.signature(specs["get_my_jira_tasks"].fn)
    assert list(signature.parameters) == []


def test_get_my_jira_tasks_splits_assigned_and_unassigned() -> None:
    assigned = JiraTask(key="WM-1", summary="A", status="To Do", assignee="Piotr")
    unassigned = JiraTask(key="WM-2", summary="B", status="To Do")
    _, specs = _catalog(tasks=[assigned, unassigned])
    result = specs["get_my_jira_tasks"].fn()
    assert [t["key"] for t in result["assigned_to_me"]] == ["WM-1"]
    assert [t["key"] for t in result["reported_by_me_unassigned"]] == ["WM-2"]
    assert result["count"] == 2


def test_get_my_jira_tasks_empty_result_is_empty_lists_not_error() -> None:
    _, specs = _catalog(tasks=[])
    result = specs["get_my_jira_tasks"].fn()
    assert result == {"assigned_to_me": [], "reported_by_me_unassigned": [], "count": 0}


def test_get_my_jira_tasks_read_error_is_enveloped_not_raised() -> None:
    _, specs = _catalog(error=JiraReadError("brak dostępu do Jiry"))
    result = specs["get_my_jira_tasks"].fn()
    assert "error" in result


def test_get_my_jira_history_serializes_tasks_and_truncated_flag() -> None:
    task = JiraTask(key="WM-3", summary="Zrobione", status="Done", resolved="2026-08-01")
    _, specs = _catalog(history=([task], True))
    result = specs["get_my_jira_history"].fn(since="2026-01-01")
    assert result["tasks"] == [task.model_dump(mode="json")]
    assert result["truncated"] is True
    assert result["count"] == 1


def test_get_my_jira_history_passes_since_until_through() -> None:
    service, specs = _catalog()
    specs["get_my_jira_history"].fn(since="2026-01-01", until="2026-12-31")
    assert service.history_calls == [("2026-01-01", "2026-12-31")]


def test_get_my_jira_history_read_error_is_enveloped_not_raised() -> None:
    _, specs = _catalog(error=JiraReadError("brak dostępu do Jiry"))
    result = specs["get_my_jira_history"].fn()
    assert "error" in result
