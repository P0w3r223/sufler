"""Testy ``TeamScheduleService`` (ADR 0059) — atrapa portu grafiku, żadnej sieci."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from workmate.core.application.team_schedule import TeamScheduleService
from workmate.core.errors import InvalidRequestError

_MEMBERS = [
    {"userId": "U1", "displayName": "Jerzy Zastepski"},
    {"userId": "U2", "displayName": "Basia Kowalska"},
]


class _FakeScheduleRead:
    def __init__(
        self,
        members: list[dict] | None = None,
        shifts: list[dict] | None = None,
        times_off: list[dict] | None = None,
        reasons: dict[str, str] | None = None,
    ) -> None:
        self._members = members if members is not None else _MEMBERS
        self._shifts = shifts or []
        self._times_off = times_off or []
        self._reasons = reasons or {}

    def list_members(self, team_id: str) -> list[dict]:
        return self._members

    def list_shifts(self, team_id: str, start: datetime, end: datetime) -> list[dict]:
        return self._shifts

    def list_times_off(self, team_id: str, start: datetime, end: datetime) -> list[dict]:
        return self._times_off

    def list_time_off_reasons(self, team_id: str) -> dict[str, str]:
        return self._reasons


def _service(**kwargs) -> TeamScheduleService:
    return TeamScheduleService(_FakeScheduleRead(**kwargs), team_id="team-1", tz="Europe/Warsaw")


def test_schedule_returns_range_and_timezone() -> None:
    result = _service().schedule(week="current")
    assert result["timezone"] == "Europe/Warsaw"
    assert "from" in result["range"] and "to" in result["range"]


def test_schedule_maps_shifts_in_window() -> None:
    shifts = [
        {
            "userId": "U1",
            "sharedShift": {
                "startDateTime": "2026-08-04T06:00:00Z",
                "endDateTime": "2026-08-04T14:00:00Z",
            },
        }
    ]
    result = _service(shifts=shifts).schedule(date_from="2026-08-01", date_to="2026-08-10")
    assert len(result["shifts"]) == 1
    assert result["shifts"][0]["person"] == "Jerzy Zastepski"


def test_schedule_narrows_to_one_person_by_name() -> None:
    shifts = [
        {
            "userId": "U1",
            "sharedShift": {
                "startDateTime": "2026-08-04T06:00:00Z",
                "endDateTime": "2026-08-04T14:00:00Z",
            },
        },
        {
            "userId": "U2",
            "sharedShift": {
                "startDateTime": "2026-08-04T06:00:00Z",
                "endDateTime": "2026-08-04T14:00:00Z",
            },
        },
    ]
    result = _service(shifts=shifts).schedule(
        date_from="2026-08-01", date_to="2026-08-10", person="Jerzy Zastepski"
    )
    assert [s["person"] for s in result["shifts"]] == ["Jerzy Zastepski"]
    # Zawężenie do osoby: "kto bez wpisów" nie ma sensu — lista wraca pusta.
    assert result["people_without_entries"] == []


def test_schedule_unknown_person_raises_invalid_request_error() -> None:
    with pytest.raises(InvalidRequestError):
        _service().schedule(date_from="2026-08-01", date_to="2026-08-10", person="Nikt Taki")


def test_schedule_ambiguous_person_raises_invalid_request_error() -> None:
    members = [
        {"userId": "U1", "displayName": "Jerzy Zastepski"},
        {"userId": "U2", "displayName": "Jerzy Nowak"},
    ]
    with pytest.raises(InvalidRequestError):
        _service(members=members).schedule(
            date_from="2026-08-01", date_to="2026-08-10", person="Jerzy"
        )


def test_schedule_lists_people_without_entries_when_unscoped() -> None:
    result = _service().schedule(date_from="2026-08-01", date_to="2026-08-10")
    assert set(result["people_without_entries"]) == {"Jerzy Zastepski", "Basia Kowalska"}


def test_schedule_maps_times_off_with_translated_reason() -> None:
    times_off = [
        {
            "userId": "U1",
            "sharedTimeOff": {
                "startDateTime": "2026-08-05T00:00:00Z",
                "endDateTime": "2026-08-06T00:00:00Z",
                "timeOffReasonId": "R1",
            },
        }
    ]
    result = _service(times_off=times_off, reasons={"R1": "Urlop"}).schedule(
        date_from="2026-08-01", date_to="2026-08-10"
    )
    assert result["times_off"][0]["reason"] == "Urlop"


# --- Sufit wpisów: cisza o przycięciu robiła z odpowiedzi wewnętrzną sprzeczność ---


def _duzo_zmian(ile: int) -> list[dict]:
    """Tyle zmian jednej osoby, ile trzeba, żeby przekroczyć sufit odpowiedzi."""
    return [
        {
            "userId": "U1",
            "sharedShift": {
                "startDateTime": f"2026-08-{1 + i % 28:02d}T{i % 12:02d}:00:00Z",
                "endDateTime": f"2026-08-{1 + i % 28:02d}T{(i % 12) + 1:02d}:00:00Z",
            },
        }
        for i in range(ile)
    ]


def test_schedule_SIGNALS_that_the_result_was_cut_instead_of_cutting_it_silently() -> None:
    """Ciche przycięcie robiło z dwóch pól tej samej odpowiedzi wzajemną sprzeczność.

    ``people_without_entries`` liczy się z PEŁNYCH list, więc osoba mająca same wpisy poza
    sufitem nie pojawiała się ani w `shifts`, ani wśród „bez wpisów" — model dostawał wycinek
    bez żadnego znaku, że to wycinek, i przedstawiał go jako całość grafiku.
    """
    from workmate.core.application.team_schedule import _MAX_ENTRIES

    result = _service(shifts=_duzo_zmian(_MAX_ENTRIES + 25)).schedule(
        date_from="2026-08-01", date_to="2026-08-28"
    )

    assert len(result["shifts"]) == _MAX_ENTRIES
    assert result["truncated"] is True
    assert result["omitted_entries"] == 25


def test_schedule_that_fits_is_NOT_flagged_as_truncated() -> None:
    """Flaga ma znaczyć „coś ucięto", a nie „grafik jest duży" — inaczej model ostrzega zawsze."""
    result = _service(shifts=_duzo_zmian(3)).schedule(date_from="2026-08-01", date_to="2026-08-28")

    assert result["truncated"] is False
    assert result["omitted_entries"] == 0


def test_the_clock_is_INJECTED_so_the_current_week_is_testable() -> None:
    """„Bieżący tydzień" jest funkcją chwili — rdzeń nie ma po nią sięgać sam (jak ``worklog``)."""

    service = TeamScheduleService(
        _FakeScheduleRead(),
        team_id="team-1",
        tz="Europe/Warsaw",
        now=lambda: datetime(2026, 8, 5, 12, 0, tzinfo=UTC),  # środa
    )

    result = service.schedule(week="current")

    assert result["range"] == {"from": "2026-08-03", "to": "2026-08-09"}
