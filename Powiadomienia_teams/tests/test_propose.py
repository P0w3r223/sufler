from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from powiadomienia_teams.domain.models import Shift
from powiadomienia_teams.reminders.propose import proposal_from_last_week

UTC = timezone.utc
WAW = ZoneInfo("Europe/Warsaw")


def _shift(
    user_id: str,
    day: int,
    h1: int,
    h2: int,
    group: str | None = "TAG_x",
    theme: str | None = "blue",
) -> Shift:
    return Shift(
        user_id,
        datetime(2026, 7, day, h1, tzinfo=UTC),
        datetime(2026, 7, day, h2, tzinfo=UTC),
        scheduling_group_id=group,
        theme=theme,
    )


def test_theme_preserved():
    last = [_shift("u1", 13, 8, 16, theme="green")]
    ws = proposal_from_last_week("u1", last, date(2026, 7, 20), tz=WAW)
    assert ws.shifts[0].theme == "green"


def test_shifts_moved_by_one_week_preserving_time_and_group():
    last = [_shift("u1", 13, 8, 16, "TAG_a"), _shift("u1", 15, 10, 20, "TAG_a")]
    ws = proposal_from_last_week("u1", last, date(2026, 7, 20), tz=WAW)
    assert ws.member_id == "u1"
    assert ws.week_start == date(2026, 7, 20)
    assert len(ws.shifts) == 2
    assert ws.shifts[0].start == datetime(2026, 7, 20, 8, tzinfo=UTC)
    assert ws.shifts[0].end == datetime(2026, 7, 20, 16, tzinfo=UTC)
    assert ws.shifts[0].scheduling_group_id == "TAG_a"
    assert ws.shifts[1].start == datetime(2026, 7, 22, 10, tzinfo=UTC)


def test_only_own_shifts_used():
    last = [_shift("u1", 13, 8, 16), _shift("u2", 13, 8, 16)]
    ws = proposal_from_last_week("u1", last, date(2026, 7, 20), tz=WAW)
    assert len(ws.shifts) == 1
    assert ws.shifts[0].user_id == "u1"


def test_no_last_week_shifts_gives_empty_schedule():
    ws = proposal_from_last_week("u1", [], date(2026, 7, 20), tz=WAW)
    assert ws.is_empty


def test_result_sorted_by_start():
    last = [_shift("u1", 15, 10, 20), _shift("u1", 13, 8, 16)]
    ws = proposal_from_last_week("u1", last, date(2026, 7, 20), tz=WAW)
    assert ws.shifts[0].start < ws.shifts[1].start


def test_preserves_local_hour_across_dst_fallback():
    # zeszły tydzień w CEST (UTC+2); cel po zmianie czasu 2026-10-25 → CET (UTC+1).
    # Sztywne +7 dni w UTC dałoby lokalnie 07:00; poprawnie ma zostać 08:00.
    last = [
        Shift(
            "u1",
            datetime(2026, 10, 22, 8, tzinfo=WAW).astimezone(UTC),
            datetime(2026, 10, 22, 16, tzinfo=WAW).astimezone(UTC),
        )
    ]
    ws = proposal_from_last_week("u1", last, date(2026, 10, 26), tz=WAW)
    s = ws.shifts[0]
    assert s.start.astimezone(WAW).hour == 8
    assert s.end.astimezone(WAW).hour == 16
    assert s.start.astimezone(WAW).date() == date(2026, 10, 29)
