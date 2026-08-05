"""Testy katalogu narzędzia grafiku Teams Shifts (ADR 0056: get_team_schedule)."""

from __future__ import annotations

from workmate.core.application.tools import build_team_schedule_catalog
from workmate.core.errors import ScheduleReadError


class _FakeTeamScheduleService:
    """Atrapa ``TeamScheduleService`` — strukturalnie zgodna, bez portu/tz realnego."""

    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self._result = result or {"shifts": [], "times_off": []}
        self._error = error
        self.calls: list[dict] = []

    def schedule(self, week="current", date_from="", date_to="", person="") -> dict:
        self.calls.append(
            {"week": week, "date_from": date_from, "date_to": date_to, "person": person}
        )
        if self._error:
            raise self._error
        return self._result


def _catalog(**kwargs):
    service = _FakeTeamScheduleService(**kwargs)
    return service, {spec.name: spec for spec in build_team_schedule_catalog(service)}  # type: ignore[arg-type]


def test_catalog_exposes_exactly_one_tool() -> None:
    _, specs = _catalog()
    assert set(specs) == {"get_team_schedule"}


def test_get_team_schedule_returns_service_result() -> None:
    result = {"shifts": [{"person": "Adam"}], "times_off": []}
    _, specs = _catalog(result=result)
    assert specs["get_team_schedule"].fn() == result


def test_get_team_schedule_passes_parameters_through() -> None:
    service, specs = _catalog()
    specs["get_team_schedule"].fn(week="next", person="Jerzy Zastepski")
    assert service.calls == [
        {"week": "next", "date_from": "", "date_to": "", "person": "Jerzy Zastepski"}
    ]


def test_get_team_schedule_error_is_enveloped_not_raised() -> None:
    _, specs = _catalog(error=ScheduleReadError("brak zgody na grafik"))
    result = specs["get_team_schedule"].fn()
    assert "error" in result
