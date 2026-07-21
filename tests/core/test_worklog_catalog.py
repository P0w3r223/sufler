"""Testy katalogu narzędzia propozycji czasu (ADR 0034, część odczytowa).

Katalog jest cienki, ale niesie inwarianty warte przypięcia: koperta zamienia
``InvalidRequestError`` w ``{"error": ...}`` (jedna zła prośba nie wywraca tury), opis narzędzia
= docstring, a po wycięciu ścieżki zapisu powierzchnia ma być JEDNONARZĘDZIOWA i tylko odczytowa.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from workmate.core.application.tools import build_worklog_catalog
from workmate.core.domain.worklog import SessionPolicy, build_proposal
from workmate.core.errors import InvalidRequestError


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


def _catalog(error: Exception | None = None):
    service = _FakeWorklogService(error)
    return service, {spec.name: spec for spec in build_worklog_catalog(service)}  # type: ignore[arg-type]


def test_catalog_exposes_only_the_read_tool() -> None:
    """Ścieżka zapisu wycięta — obecność ``log_jira_worklog`` byłaby regresją, nie dodatkiem."""
    _, specs = _catalog()
    assert set(specs) == {"propose_worklog"}


def test_description_comes_from_the_docstring() -> None:
    _, specs = _catalog()
    assert "ODCZYT" in specs["propose_worklog"].description


def test_description_tells_the_model_that_nothing_can_be_written() -> None:
    """Model musi wiedzieć, że nie ma dokąd zapisać godzin — inaczej będzie szukał narzędzia."""
    _, specs = _catalog()
    assert "człowiek" in specs["propose_worklog"].description


def test_proposal_is_serialized_to_plain_json() -> None:
    _, specs = _catalog()
    result = specs["propose_worklog"].fn(date(2026, 7, 13), date(2026, 7, 19))
    assert result["since"] == "2026-07-13"
    assert result["sessions"] == []


def test_read_error_is_enveloped_not_raised() -> None:
    _, specs = _catalog(InvalidRequestError("zakres odrzucony"))
    assert "error" in specs["propose_worklog"].fn(date(2026, 7, 13), date(2026, 7, 19))


def test_arguments_are_forwarded() -> None:
    service, specs = _catalog()
    specs["propose_worklog"].fn(date(2026, 7, 13), date(2026, 7, 19), "P0w3r223")
    assert service.calls[0] == (date(2026, 7, 13), date(2026, 7, 19), "P0w3r223")
