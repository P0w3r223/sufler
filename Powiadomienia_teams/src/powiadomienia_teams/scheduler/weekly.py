"""Wyznaczanie kolejnego terminu uruchomienia (domyślnie niedziela 16:00 Europe/Warsaw).

`next_run` jest czysta (wstrzykiwany `now`) i odporna na zmianę czasu (DST): wall-clock
budowany jest przez `datetime.combine(..., tzinfo=tz)`, więc 16:00 zawsze oznacza lokalne 16:00.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def next_run(
    now: datetime,
    *,
    tz: ZoneInfo,
    weekday: int = 6,
    hour: int = 16,
    minute: int = 0,
) -> datetime:
    """Najbliższy `weekday` o godzinie `hour`:`minute` w strefie `tz`, ściśle po `now`.

    weekday: 0=poniedziałek … 6=niedziela (jak `datetime.weekday()`).
    `now` może być w dowolnej strefie — zostanie przeliczone do `tz`.
    Zwraca datetime tz-aware w `tz`.
    """
    local_now = now.astimezone(tz)
    days_ahead = (weekday - local_now.weekday()) % 7
    target_date = local_now.date() + timedelta(days=days_ahead)
    candidate = datetime.combine(target_date, time(hour, minute), tzinfo=tz)
    if candidate <= local_now:
        candidate = datetime.combine(target_date + timedelta(days=7), time(hour, minute), tzinfo=tz)
    return candidate
