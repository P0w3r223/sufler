"""Budowa propozycji »jak w zeszłym tygodniu« (czysta logika)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from powiadomienia_teams.domain.models import Shift, WeekSchedule


def _plus_one_week_local(dt: datetime, tz: ZoneInfo) -> datetime:
    """Przesuń o 7 dni ZACHOWUJĄC lokalną porę dnia (poprawnie przez zmianę czasu / DST).

    Przesunięcie liczone jest na ścianie zegara w `tz`, nie na instancie UTC — dzięki temu
    zmiana 08:00–16:00 lokalnie pozostaje 08:00–16:00 także w tygodniu po zmianie czasu
    (inaczej wynik przesunąłby się o godzinę). Wynik zwracany w UTC (jak dane z Graph).
    """
    local_next = dt.astimezone(tz).replace(tzinfo=None) + timedelta(days=7)
    return local_next.replace(tzinfo=tz).astimezone(timezone.utc)


def proposal_from_last_week(
    member_id: str,
    last_week_shifts: Iterable[Shift],
    target_week_start: date,
    *,
    tz: ZoneInfo,
    skip_weekdays: frozenset[int] = frozenset(),
) -> WeekSchedule:
    """Przesuń zmiany pracownika z zeszłego tygodnia o 7 dni na `target_week_start`.

    Zachowuje lokalną porę dnia (patrz `_plus_one_week_local` — DST), długość i grupę grafiku.
    Bierze pod uwagę wyłącznie zmiany danego `member_id`. Brak zmian w zeszłym tygodniu →
    pusty WeekSchedule (nie ma z czego zaproponować »jak ostatnio«).

    `skip_weekdays` to dni (0=pon…6=nd), w których osoba ma urlop w docelowym tygodniu — nie
    proponujemy w nie pracy. Przesunięcie o 7 dni zachowuje weekday, więc filtrujemy po weekdayu
    zmiany źródłowej (w strefie `tz`), co jest równoważne dniowi docelowemu.
    """
    own = sorted(
        (
            s
            for s in last_week_shifts
            if s.user_id == member_id and s.start.astimezone(tz).weekday() not in skip_weekdays
        ),
        key=lambda s: s.start,
    )
    shifted = tuple(
        Shift(
            user_id=member_id,
            start=_plus_one_week_local(s.start, tz),
            end=_plus_one_week_local(s.end, tz),
            scheduling_group_id=s.scheduling_group_id,
            theme=s.theme,  # kolor = tryb pracy — kopiujemy z dnia źródłowego
        )
        for s in own
    )
    return WeekSchedule(member_id=member_id, week_start=target_week_start, shifts=shifted)
