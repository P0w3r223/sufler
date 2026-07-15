from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from powiadomienia_teams.scheduler.weekly import next_run

WAW = ZoneInfo("Europe/Warsaw")


def test_next_sunday_from_midweek():
    now = datetime(2026, 7, 14, 10, 0, tzinfo=WAW)  # wtorek
    r = next_run(now, tz=WAW)
    assert (r.year, r.month, r.day, r.hour, r.minute) == (2026, 7, 19, 16, 0)
    assert r.weekday() == 6


def test_same_sunday_before_hour():
    now = datetime(2026, 7, 19, 15, 0, tzinfo=WAW)
    r = next_run(now, tz=WAW)
    assert r.day == 19 and r.hour == 16


def test_sunday_exactly_at_hour_rolls_forward():
    now = datetime(2026, 7, 19, 16, 0, tzinfo=WAW)
    r = next_run(now, tz=WAW)
    assert r.day == 26


def test_sunday_after_hour_goes_next_week():
    now = datetime(2026, 7, 19, 17, 0, tzinfo=WAW)
    r = next_run(now, tz=WAW)
    assert r.day == 26


def test_now_in_utc_is_converted():
    now = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)  # 10:00 w Warszawie
    r = next_run(now, tz=WAW)
    assert (r.month, r.day, r.hour) == (7, 19, 16)


def test_dst_summer_offset_is_plus_two():
    r = next_run(datetime(2026, 8, 1, 12, 0, tzinfo=WAW), tz=WAW)
    assert r.utcoffset() == timedelta(hours=2)


def test_dst_winter_offset_is_plus_one():
    r = next_run(datetime(2026, 12, 1, 12, 0, tzinfo=WAW), tz=WAW)
    assert r.utcoffset() == timedelta(hours=1)


def test_custom_weekday_and_hour():
    now = datetime(2026, 7, 14, 10, 0, tzinfo=WAW)  # wtorek
    r = next_run(now, tz=WAW, weekday=4, hour=9)  # piątek 09:00
    assert r.weekday() == 4 and r.hour == 9 and r.day == 17
