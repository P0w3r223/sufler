"""Testy katalogu narzędzia "moje zadania" Jira (ADR 0054, część odczytowa).

Katalog jest cienki, ale niesie inwarianty warte przypięcia: brak parametrów (nie da się nim
podejrzeć cudzych zadań), koperta zamienia błędy na ``{"error": ...}``, wynik jest listą pod
kluczem ``tasks``.
"""

from __future__ import annotations

import inspect

from workmate.core.application.tools import build_my_jira_tasks_catalog
from workmate.core.domain.jira_tasks import JiraTask
from workmate.core.errors import JiraReadError


class _FakeMyJiraTasksService:
    def __init__(self, tasks: list[JiraTask] | None = None, error: Exception | None = None) -> None:
        self._tasks = tasks or []
        self._error = error
        self.calls = 0

    def my_open_tasks(self) -> list[JiraTask]:
        self.calls += 1
        if self._error:
            raise self._error
        return self._tasks


def _catalog(**kwargs):
    service = _FakeMyJiraTasksService(**kwargs)
    return service, {spec.name: spec for spec in build_my_jira_tasks_catalog(service)}  # type: ignore[arg-type]


def test_catalog_exposes_exactly_one_tool() -> None:
    _, specs = _catalog()
    assert set(specs) == {"get_my_jira_tasks"}


def test_tool_takes_no_parameters() -> None:
    """Zero parametrów jest kontraktem bezpieczeństwa: nie da się poprosić o cudze zadania."""
    _, specs = _catalog()
    signature = inspect.signature(specs["get_my_jira_tasks"].fn)
    assert list(signature.parameters) == []


def test_tasks_are_serialized_under_tasks_key() -> None:
    task = JiraTask(key="WM-1", summary="Zrobić X", status="To Do")
    _, specs = _catalog(tasks=[task])
    result = specs["get_my_jira_tasks"].fn()
    assert result["tasks"] == [task.model_dump(mode="json")]


def test_empty_result_is_empty_list_not_error() -> None:
    _, specs = _catalog(tasks=[])
    result = specs["get_my_jira_tasks"].fn()
    assert result == {"tasks": []}


def test_read_error_is_enveloped_not_raised() -> None:
    _, specs = _catalog(error=JiraReadError("brak dostępu do Jiry"))
    result = specs["get_my_jira_tasks"].fn()
    assert "error" in result
