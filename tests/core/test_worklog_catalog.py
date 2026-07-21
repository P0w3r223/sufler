"""Testy katalogu narzędzi ewidencji czasu (ADR 0034).

Katalog jest cienki, ale niesie dwa inwarianty warte przypięcia: koperta zamienia ``WriteError``
w ``{"error": ...}`` (jedna zła prośba nie wywraca tury) oraz opis narzędzia = docstring.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from workmate.core.application.tools import build_worklog_catalog
from workmate.core.domain.worklog import SessionPolicy, build_proposal
from workmate.core.errors import WriteError


class _FakeWorklogService:
    """Atrapa serwisu — sterowana wyjątkiem, żeby sprawdzić kopertę bez stawiania portów."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[Any, ...]] = []

    def propose_worklog(self, since: date, until: date, author: str = ""):
        self.calls.append((since, until, author))
        if self.error:
            raise self.error
        return build_proposal([], since=since, until=until, policy=SessionPolicy())

    def log_jira_worklog(
        self,
        issue_key: str,
        hours: float,
        on: date,
        comment: str = "",
        on_behalf_of: str = "",
        display_name: str = "",
    ) -> dict[str, Any]:
        self.calls.append((issue_key, hours, on, comment, on_behalf_of, display_name))
        if self.error:
            raise self.error
        return {"logged": True, "issue_key": issue_key}


def _catalog(error: Exception | None = None):
    service = _FakeWorklogService(error)
    return service, {spec.name: spec for spec in build_worklog_catalog(service)}  # type: ignore[arg-type]


def test_catalog_exposes_exactly_the_two_worklog_tools() -> None:
    _, specs = _catalog()
    assert set(specs) == {"propose_worklog", "log_jira_worklog"}


def test_descriptions_come_from_docstrings() -> None:
    _, specs = _catalog()
    assert "ODCZYT" in specs["propose_worklog"].description
    assert "NIEODWRACALNY" in specs["log_jira_worklog"].description


def test_write_tool_description_warns_about_attribution() -> None:
    """Model MUSI widzieć w opisie, że ``on_behalf_of`` nie zmienia autora w Jirze."""
    _, specs = _catalog()
    assert "konto tokenu" in specs["log_jira_worklog"].description


def test_proposal_is_serialized_to_plain_json() -> None:
    _, specs = _catalog()
    result = specs["propose_worklog"].fn(date(2026, 7, 13), date(2026, 7, 19))
    assert result["since"] == "2026-07-13"
    assert result["sessions"] == []


def test_write_error_is_enveloped_not_raised() -> None:
    _, specs = _catalog(WriteError("klucz odrzucony"))
    assert specs["log_jira_worklog"].fn("OPS-1", 2.0, date(2026, 7, 19)) == {
        "error": "klucz odrzucony"
    }


def test_read_error_is_enveloped_too() -> None:
    _, specs = _catalog(WriteError("zakres odrzucony"))
    assert "error" in specs["propose_worklog"].fn(date(2026, 7, 13), date(2026, 7, 19))


def test_write_tool_forwards_all_arguments() -> None:
    service, specs = _catalog()
    specs["log_jira_worklog"].fn("WT-1", 1.5, date(2026, 7, 19), "opis", "acc-1", "Mikołaj")
    assert service.calls[0] == ("WT-1", 1.5, date(2026, 7, 19), "opis", "acc-1", "Mikołaj")
