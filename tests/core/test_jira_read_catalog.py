"""Testy katalogu narzędzi rozszerzonego odczytu Jiry (ADR 0059: get_jira_task, search_jira_tasks,
get_member_jira_tasks, get_member_jira_history).

Sedno: cztery narzędzia, koperta błędów, i — dla narzędzi "member" — fail-closed odmowa gdy
``resolve_member`` (zaufana mapa tożsamości) nie rozpoznaje jednoznacznie osoby.
"""

from __future__ import annotations

from workmate.core.application.tools import build_jira_read_catalog
from workmate.core.domain.jira_tasks import JiraTask, JiraTaskDetails
from workmate.core.errors import JiraReadError, WorkMateError


class _FakeJiraReadService:
    def __init__(
        self,
        details: JiraTaskDetails | None = None,
        tasks: list[JiraTask] | None = None,
        history: tuple[list[JiraTask], bool] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._details = details or JiraTaskDetails(key="WT-1", summary="x")
        self._tasks = tasks or []
        self._history = history or ([], False)
        self._error = error
        self.member_open_calls: list[str] = []
        self.member_history_calls: list[tuple[str, str, str]] = []

    def task_details(self, key: str) -> JiraTaskDetails:
        if self._error:
            raise self._error
        return self._details

    def search_tasks(self, text="", project="", status_category="", limit=20) -> list[JiraTask]:
        if self._error:
            raise self._error
        return self._tasks

    def member_open_tasks(self, jira_user: str) -> list[JiraTask]:
        self.member_open_calls.append(jira_user)
        if self._error:
            raise self._error
        return self._tasks

    def member_history(self, jira_user: str, since: str = "", until: str = ""):
        self.member_history_calls.append((jira_user, since, until))
        if self._error:
            raise self._error
        return self._history


def _resolver(known: dict[str, str]):
    def resolve(name: str) -> str | None:
        return known.get(name)

    return resolve


def _catalog(service=None, resolver=None):
    service = service or _FakeJiraReadService()
    resolver = resolver or _resolver({})
    return service, {spec.name: spec for spec in build_jira_read_catalog(service, resolver)}


def test_catalog_exposes_four_tools() -> None:
    _, specs = _catalog()
    assert set(specs) == {
        "get_jira_task",
        "search_jira_tasks",
        "get_member_jira_tasks",
        "get_member_jira_history",
    }


# --- get_jira_task -----------------------------------------------------------------


def test_get_jira_task_returns_serialized_details() -> None:
    details = JiraTaskDetails(key="WT-9", summary="Coś")
    _, specs = _catalog(_FakeJiraReadService(details=details))
    result = specs["get_jira_task"].fn(key="WT-9")
    assert result["key"] == "WT-9"


def test_get_jira_task_error_is_enveloped() -> None:
    _, specs = _catalog(_FakeJiraReadService(error=JiraReadError("boom")))
    result = specs["get_jira_task"].fn(key="WT-9")
    assert "error" in result


# --- search_jira_tasks ---------------------------------------------------------------


def test_search_jira_tasks_returns_count_and_tasks() -> None:
    task = JiraTask(key="WT-1", summary="A", status="To Do")
    _, specs = _catalog(_FakeJiraReadService(tasks=[task]))
    result = specs["search_jira_tasks"].fn(query="scada")
    assert result["count"] == 1
    assert result["tasks"] == [task.model_dump(mode="json")]


def test_search_jira_tasks_invalid_filters_enveloped_as_error() -> None:
    """``search_tasks`` propaguje ``InvalidRequestError`` (WorkMateError) → koperta, nie wyjątek."""
    from workmate.core.errors import InvalidRequestError

    class _RaisingService(_FakeJiraReadService):
        def search_tasks(self, *a, **kw):
            raise InvalidRequestError("brak filtrów")

    assert issubclass(InvalidRequestError, WorkMateError)
    _, specs = _catalog(_RaisingService())
    result = specs["search_jira_tasks"].fn()
    assert "error" in result


# --- get_member_jira_tasks: fail-closed na nieznaną/niejednoznaczną osobę ------------


def test_get_member_jira_tasks_known_member_splits_groups() -> None:
    assigned = JiraTask(key="WT-1", summary="A", status="To Do", assignee="Ona")
    unassigned = JiraTask(key="WT-2", summary="B", status="To Do")
    service = _FakeJiraReadService(tasks=[assigned, unassigned])
    resolver = _resolver({"Znana Osoba": "znana@example.org"})
    _, specs = _catalog(service, resolver)
    result = specs["get_member_jira_tasks"].fn(member="Znana Osoba")
    assert [t["key"] for t in result["assigned"]] == ["WT-1"]
    assert [t["key"] for t in result["reported_unassigned"]] == ["WT-2"]
    assert service.member_open_calls == ["znana@example.org"]


def test_get_member_jira_tasks_unknown_member_is_readable_refusal() -> None:
    _, specs = _catalog(resolver=_resolver({}))
    result = specs["get_member_jira_tasks"].fn(member="Ktoś Inny")
    assert "error" in result


# --- get_member_jira_history: fail-closed, truncated flag ----------------------------


def test_get_member_jira_history_known_member_returns_tasks() -> None:
    task = JiraTask(key="WT-3", summary="Zrobione", status="Done", resolved="2026-08-01")
    service = _FakeJiraReadService(history=([task], False))
    resolver = _resolver({"Znana Osoba": "znana@example.org"})
    _, specs = _catalog(service, resolver)
    result = specs["get_member_jira_history"].fn(member="Znana Osoba", since="2026-01-01")
    assert result["count"] == 1
    assert result["truncated"] is False
    assert service.member_history_calls == [("znana@example.org", "2026-01-01", "")]


def test_get_member_jira_history_unknown_member_is_readable_refusal() -> None:
    _, specs = _catalog(resolver=_resolver({}))
    result = specs["get_member_jira_history"].fn(member="Ktoś Inny")
    assert "error" in result
