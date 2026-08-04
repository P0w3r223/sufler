"""Testy czystej domeny grafiku Teams Shifts (ADR 0056) — zakres dat, mapowanie, strefa, bez I/O."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from workmate.core.domain.schedule import (
    ShiftEntry,
    map_shifts,
    map_times_off,
    resolve_schedule_range,
)
from workmate.core.errors import InvalidRequestError

_TZ = ZoneInfo("Europe/Warsaw")
_MONDAY = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)  # poniedziałek


# --- resolve_schedule_range ---------------------------------------------------


def test_range_current_week_is_monday_to_next_monday() -> None:
    start, end = resolve_schedule_range("current", today=_MONDAY, tz=_TZ)
    assert start.date().isoformat() == "2026-08-03"
    assert end.date().isoformat() == "2026-08-10"


def test_range_previous_week_goes_back_seven_days() -> None:
    start, _ = resolve_schedule_range("previous", today=_MONDAY, tz=_TZ)
    assert start.date().isoformat() == "2026-07-27"


def test_range_next_week_goes_forward_seven_days() -> None:
    start, _ = resolve_schedule_range("next", today=_MONDAY, tz=_TZ)
    assert start.date().isoformat() == "2026-08-10"


def test_range_explicit_dates_are_inclusive_of_end_day() -> None:
    _, end = resolve_schedule_range(
        date_from="2026-08-01", date_to="2026-08-05", today=_MONDAY, tz=_TZ
    )
    assert end.date().isoformat() == "2026-08-06"  # półotwarty: end = dzień_do + 1


def test_range_rejects_span_over_max_days() -> None:
    with pytest.raises(InvalidRequestError):
        resolve_schedule_range(date_from="2026-08-01", date_to="2026-10-01", today=_MONDAY, tz=_TZ)


def test_range_rejects_only_one_explicit_date() -> None:
    with pytest.raises(InvalidRequestError):
        resolve_schedule_range(date_from="2026-08-01", today=_MONDAY, tz=_TZ)


def test_range_rejects_unknown_week_value() -> None:
    with pytest.raises(InvalidRequestError):
        resolve_schedule_range("wat", today=_MONDAY, tz=_TZ)


def test_range_rejects_end_before_start() -> None:
    with pytest.raises(InvalidRequestError):
        resolve_schedule_range(date_from="2026-08-05", date_to="2026-08-01", today=_MONDAY, tz=_TZ)


# --- map_shifts: overlap, drafty, strefa, work_mode ----------------------------


def test_map_shifts_keeps_only_entries_overlapping_window() -> None:
    window = resolve_schedule_range("current", today=_MONDAY, tz=_TZ)
    raw = [
        {
            "userId": "U1",
            "sharedShift": {
                "startDateTime": "2026-08-04T06:00:00Z",
                "endDateTime": "2026-08-04T14:00:00Z",
            },
        },
        {
            "userId": "U9",
            "sharedShift": {
                "startDateTime": "2026-09-01T06:00:00Z",
                "endDateTime": "2026-09-01T14:00:00Z",
            },
        },  # poza oknem
        {"userId": "U2"},  # draft (bez sharedShift) → pominięty
    ]
    shifts = map_shifts(raw, {"U1": "Jerzy Zastepski"}, window=window, tz=_TZ)
    assert len(shifts) == 1
    assert shifts[0].person == "Jerzy Zastepski"


def test_map_shifts_renders_in_warsaw_timezone() -> None:
    window = resolve_schedule_range("current", today=_MONDAY, tz=_TZ)
    raw = [
        {
            "userId": "U1",
            "sharedShift": {
                "startDateTime": "2026-08-04T06:00:00Z",
                "endDateTime": "2026-08-04T14:00:00Z",
            },
        }
    ]
    shifts = map_shifts(raw, {"U1": "Jerzy Zastepski"}, window=window, tz=_TZ)
    assert shifts[0].start == "2026-08-04 08:00"
    assert shifts[0].end == "2026-08-04 16:00"


def test_map_shifts_unknown_user_id_does_not_crash() -> None:
    window = resolve_schedule_range("current", today=_MONDAY, tz=_TZ)
    raw = [
        {
            "userId": "unknown",
            "sharedShift": {
                "startDateTime": "2026-08-04T06:00:00Z",
                "endDateTime": "2026-08-04T14:00:00Z",
            },
        }
    ]
    shifts = map_shifts(raw, {}, window=window, tz=_TZ)
    assert shifts[0].person == "(nieznany)"


@pytest.mark.parametrize(
    ("theme", "expected_mode"),
    [("green", "stacjonarnie"), ("darkBlue", "zdalnie"), ("yellow", None)],
)
def test_map_shifts_translates_theme_to_work_mode(theme: str, expected_mode: str | None) -> None:
    window = resolve_schedule_range("current", today=_MONDAY, tz=_TZ)
    raw = [
        {
            "userId": "U1",
            "sharedShift": {
                "startDateTime": "2026-08-04T06:00:00Z",
                "endDateTime": "2026-08-04T14:00:00Z",
                "theme": theme,
            },
        }
    ]
    shifts = map_shifts(raw, {"U1": "Jerzy Zastepski"}, window=window, tz=_TZ)
    assert shifts[0].theme == theme
    assert shifts[0].work_mode == expected_mode


def test_shift_entry_defaults_have_no_theme_or_work_mode() -> None:
    entry = ShiftEntry(person="x", start="a", end="b")
    assert entry.theme == ""
    assert entry.work_mode is None


# --- map_times_off --------------------------------------------------------------


def test_map_times_off_translates_reason_id_to_display_name() -> None:
    window = resolve_schedule_range("current", today=_MONDAY, tz=_TZ)
    raw = [
        {
            "userId": "U1",
            "sharedTimeOff": {
                "startDateTime": "2026-08-05T00:00:00Z",
                "endDateTime": "2026-08-06T00:00:00Z",
                "timeOffReasonId": "R1",
            },
        }
    ]
    off = map_times_off(raw, {"U1": "Jerzy Zastepski"}, {"R1": "Urlop"}, window=window, tz=_TZ)
    assert len(off) == 1
    assert off[0].reason == "Urlop"
