"""Wyznaczanie kolejnego terminu uruchomienia (domyślnie piątek 16:00 Europe/Warsaw).

`next_run` jest czysta (wstrzykiwany `now`) i odporna na zmianę czasu (DST): wall-clock
budowany jest przez `datetime.combine(..., tzinfo=tz)`, więc 16:00 zawsze oznacza lokalne 16:00.

Domyślne `weekday`/`hour` są tu WYŁĄCZNIE wygodą dla testów kalendarzowych — jedynym źródłem
prawdy jest `config.Settings` (`run_weekday`/`run_hour`/`run_minute`), które orkiestracja podaje
jawnie. Trzymamy je zgodne z tamtymi, bo rozjazd był mylący: moduł deklarował niedzielę, której
walidacja krzyżowa `Settings.validate` nie przepuszcza przy domyślnym oknie wysyłki (pn–pt).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo


def this_week_monday(now: datetime, tz: tzinfo) -> datetime:
    """Lokalna północ poniedziałku bieżącego tygodnia (tz-aware), dla dowolnej strefy `now`."""
    local = now.astimezone(tz)
    return (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def week_windows(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime, datetime]:
    """Zwróć (prior_monday, target_monday, target_end) — lokalne północe tz-aware.

    Cel = przyszły tydzień (Pon–Nd). Gotowiec = tydzień bezpośrednio przed celem (ten, który
    właśnie się kończy) = ``[prior_monday, target_monday)``.
    """
    monday = this_week_monday(now, tz)
    target_monday = monday + timedelta(days=7)
    target_end = target_monday + timedelta(days=7)
    return monday, target_monday, target_end


def next_run(
    now: datetime,
    *,
    tz: ZoneInfo,
    weekday: int = 4,
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


def previous_run(
    now: datetime,
    *,
    tz: ZoneInfo,
    weekday: int = 4,
    hour: int = 16,
    minute: int = 0,
) -> datetime:
    """Najbliższe wystąpienie `weekday` o `hour`:`minute` w strefie `tz`, w `now` lub PRZED nim.

    Odwrotność `next_run` — służy do wykrycia, czy zaplanowany termin właśnie minął (nadrobienie).
    Zwraca datetime tz-aware w `tz`.
    """
    local_now = now.astimezone(tz)
    days_behind = (local_now.weekday() - weekday) % 7
    candidate = datetime.combine(
        local_now.date() - timedelta(days=days_behind), time(hour, minute), tzinfo=tz
    )
    if candidate > local_now:
        candidate = datetime.combine(
            candidate.date() - timedelta(days=7), time(hour, minute), tzinfo=tz
        )
    return candidate
