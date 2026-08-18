"""Testy ``JiraReadService`` (ADR 0059, rozszerzony odczyt Jiry) — atrapa portu, żadnej sieci."""

from __future__ import annotations

from typing import Any

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


@pytest.mark.parametrize(
    ("metoda", "argumenty"),
    [
        ("task_details", ("WT-5",)),
        ("search_tasks", ("scada",)),  # filtr wymagany PRZED odczytem — inaczej odmowa domeny
        ("member_open_tasks", ("kolega@example.org",)),
        ("member_history", ("kolega@example.org",)),
    ],
)
def test_a_port_error_reaches_the_caller_untouched(metoda: str, argumenty: tuple) -> None:
    """Tłumaczenie httpx → ``JiraReadError`` mieszka na granicy adaptera; tu pilnujemy PRZEPŁYWU.

    Każda z tych czterech metod robi po odczycie coś jeszcze (mapowanie, przycięcie do sufitu),
    więc połknięcie awarii dałoby PUSTĄ listę — „nic nie znalazłem" zamiast „nie udało się
    zapytać". Brzmienia komunikatów sprawdzają sondy adaptera
    (``tests/adapters/test_jira_api.py``).
    """
    awaria = JiraReadError("brak dostępu do Jiry — token jest nieważny albo bez uprawnień odczytu.")
    service = JiraReadService(_FakeJiraRead(error=awaria))

    with pytest.raises(JiraReadError) as exc:
        getattr(service, metoda)(*argumenty)

    assert exc.value is awaria, "rdzeń nie ma opakowywać błędu portu w drugi, własny"


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


def test_member_open_tasks_caps_the_result_and_reports_truncation() -> None:
    """Bliźniaczo do historii: ``max_results`` jest rozmiarem STRONY, a adapter paginuje.

    Bez przycięcia po zmapowaniu `member_tasks` mogło zwrócić do kontekstu modelu wielokrotność
    sufitu, w dodatku milcząco — bez flagi, którą model miałby przekazać człowiekowi.
    """
    from workmate.core.application.my_jira_tasks import _MAX_RESULTS

    ile = _MAX_RESULTS * 4
    issues = [{"key": f"WT-{i}", "fields": {"summary": "x"}} for i in range(ile)]
    service = JiraReadService(_FakeJiraRead(issues=issues))

    tasks, truncated = service.member_open_tasks("kolega@example.org")

    assert len(tasks) == _MAX_RESULTS
    assert truncated is True


def test_member_open_tasks_shares_the_cap_with_MY_open_tasks() -> None:
    """Opis narzędzia mówi „to samo dla INNEJ osoby" — sufit też ma być ten sam.

    ``member_open_tasks`` ciął do ``_MAX_SEARCH_RESULTS`` (20), a ``my_open_tasks`` do
    ``_MAX_RESULTS`` (50) — stała wyszukiwania użyta tu chyba tylko dlatego, że leżała w tym
    samym pliku. Skutek: osoba z 30 zadaniami widziała u siebie 30, a u kolegi 20 i
    ``truncated=true``, choć narzędzie obiecuje tę samą listę dla obu.
    """
    from workmate.core.application.my_jira_tasks import _MAX_RESULTS, MyJiraTasksService

    ile = _MAX_RESULTS - 20  # mieści się u „mnie", nie mieściło się u „członka"
    issues = [{"key": f"WT-{i}", "fields": {"summary": "x"}} for i in range(ile)]

    moje, moje_uciete = MyJiraTasksService(
        _FakeJiraRead(issues=issues), assignee="ja@example.org"
    ).my_open_tasks()
    czyjes, czyjes_uciete = JiraReadService(_FakeJiraRead(issues=issues)).member_open_tasks(
        "kolega@example.org"
    )

    assert (len(moje), moje_uciete) == (len(czyjes), czyjes_uciete) == (ile, False)


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
