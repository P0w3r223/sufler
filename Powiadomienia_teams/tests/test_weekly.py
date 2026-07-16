from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from powiadomienia_teams.scheduler.weekly import next_run, previous_run, week_windows

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


def test_previous_run_this_friday_after_hour():
    now = datetime(2026, 7, 17, 17, 0, tzinfo=WAW)  # piątek 17:00 (po 16:00)
    r = previous_run(now, tz=WAW, weekday=4, hour=16)
    assert (r.month, r.day, r.hour) == (7, 17, 16)  # ten piątek 16:00


def test_previous_run_before_hour_goes_prior_week():
    now = datetime(2026, 7, 17, 15, 0, tzinfo=WAW)  # piątek 15:00 (przed 16:00)
    r = previous_run(now, tz=WAW, weekday=4, hour=16)
    assert (r.month, r.day, r.hour) == (7, 10, 16)  # poprzedni piątek 16:00


def test_previous_run_midweek_returns_last_friday():
    now = datetime(2026, 7, 15, 12, 0, tzinfo=WAW)  # środa
    r = previous_run(now, tz=WAW, weekday=4, hour=16)
    assert r.day == 10 and r.weekday() == 4  # ostatni piątek (10.07)


def test_next_and_previous_run_bracket_now():
    now = datetime(2026, 7, 15, 12, 0, tzinfo=WAW)
    prev = previous_run(now, tz=WAW, weekday=4, hour=16)
    nxt = next_run(now, tz=WAW, weekday=4, hour=16)
    assert prev < now < nxt  # now zawsze pomiędzy poprzednim a następnym terminem


def test_week_windows_from_friday_targets_next_working_week():
    # Powiadomienie w PIĄTEK 16:00 — cel = następny tydzień (pon–ndz), gotowiec = tydzień bieżący.
    # Piątek i niedziela są w tym samym tygodniu kalendarzowym, więc okno jest identyczne jak dla
    # niedzieli (zmiana terminu na piątek nie rusza logiki week_windows).
    now = datetime(2026, 7, 17, 16, 0, tzinfo=WAW)  # piątek 17.07
    prior, target, target_end = week_windows(now, WAW)
    assert prior.date().isoformat() == "2026-07-13"     # bieżący tydzień = gotowiec
    assert target.date().isoformat() == "2026-07-20"    # następny tydzień = cel
    assert target_end.date().isoformat() == "2026-07-27"
    assert target.weekday() == 0  # poniedziałek
