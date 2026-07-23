"""Grupowanie po dniu — kluczowy przypadek granicy doby UTC→lokalna."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from claude_summary.core.grouping import group_by_day
from claude_summary.core.models import Prompt

WARSAW = ZoneInfo("Europe/Warsaw")


def _prompt(timestamp: str) -> Prompt:
    return Prompt(
        timestamp=datetime.fromisoformat(timestamp),
        text="x",
        session_id="s",
        cwd="",
        project="p",
    )


def test_late_evening_utc_moves_to_next_local_day() -> None:
    # 22:30 UTC 17 lipca = 00:30 CEST 18 lipca (Europe/Warsaw = UTC+2 latem).
    prompt = _prompt("2026-07-17T22:30:00+00:00")
    days = group_by_day([prompt], [], tz=WARSAW, since=date(2026, 7, 17), until=date(2026, 7, 18))
    by_date = {day.day: day for day in days}
    assert by_date[date(2026, 7, 17)].prompt_count == 0
    assert by_date[date(2026, 7, 18)].prompt_count == 1


def test_full_range_present_even_when_empty() -> None:
    days = group_by_day([], [], tz=WARSAW, since=date(2026, 7, 17), until=date(2026, 7, 19))
    assert [day.day for day in days] == [date(2026, 7, 17), date(2026, 7, 18), date(2026, 7, 19)]
    assert all(day.is_empty for day in days)


def test_out_of_range_ignored() -> None:
    prompt = _prompt("2026-07-10T09:00:00+00:00")
    days = group_by_day([prompt], [], tz=WARSAW, since=date(2026, 7, 17), until=date(2026, 7, 18))
    assert sum(day.prompt_count for day in days) == 0


def test_prompts_sorted_within_day() -> None:
    late = _prompt("2026-07-17T12:00:00+00:00")
    early = _prompt("2026-07-17T06:00:00+00:00")
    one_day = date(2026, 7, 17)
    days = group_by_day([late, early], [], tz=WARSAW, since=one_day, until=one_day)
    times = [prompt.timestamp for prompt in days[0].prompts]
    assert times == sorted(times)
