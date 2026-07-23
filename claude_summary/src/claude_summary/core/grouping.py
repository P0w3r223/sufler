"""Czysta agregacja promptów i commitów w dni kalendarzowe strefy lokalnej.

Jedyne miejsce konwersji UTC→lokalna: znacznik czasu przeliczamy na strefę ``tz`` DOPIERO tutaj,
przed obcięciem do daty. Bez tego wieczorny prompt zapisany w UTC (np. 22:30Z) trafiłby do
poprzedniego dnia zamiast do właściwego dnia lokalnego (00:30 następnego dnia w Europie/Warszawie).
Raport pokrywa CAŁY zakres ``since..until`` — dni bez aktywności też mają swój (pusty) wpis,
żeby tygodniowy obraz był kompletny.
"""

from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from claude_summary.core.models import Commit, DaySummary, Prompt


def _date_range(since: date, until: date) -> list[date]:
    days: list[date] = []
    current = since
    while current <= until:
        days.append(current)
        current += timedelta(days=1)
    return days


def group_by_day(
    prompts: list[Prompt],
    commits: list[Commit],
    *,
    tz: ZoneInfo,
    since: date,
    until: date,
) -> list[DaySummary]:
    """Pogrupuj prompty i commity po dacie lokalnej; jeden ``DaySummary`` na każdy dzień zakresu."""
    span = _date_range(since, until)
    allowed = set(span)
    prompts_by_day: dict[date, list[Prompt]] = {day: [] for day in span}
    commits_by_day: dict[date, list[Commit]] = {day: [] for day in span}

    for prompt in prompts:
        day = prompt.timestamp.astimezone(tz).date()
        if day in allowed:
            prompts_by_day[day].append(prompt)
    for commit in commits:
        day = commit.timestamp.astimezone(tz).date()
        if day in allowed:
            commits_by_day[day].append(commit)

    return [
        DaySummary(
            day=day,
            prompts=tuple(sorted(prompts_by_day[day], key=lambda item: item.timestamp)),
            commits=tuple(sorted(commits_by_day[day], key=lambda item: item.timestamp)),
        )
        for day in span
    ]
