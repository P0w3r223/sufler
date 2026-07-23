"""Godziny z bloków zmian (Microsoft Shifts) → minuty per (osoba, dzień) — ADR 0036.

CZYSTA agregacja: bez I/O, bez SDK, bez zegara. Wejściem są bloki zmian już odczytane przez adapter
Graph (``ShiftSource``), wyjściem minuty przepracowane w każdej LOKALNEJ dobie tygodnia raportowego.

Uczciwość (kontrast z ADR 0034): tu NIE MA estymacji. Minuty to realny czas OPUBLIKOWANYCH zmian
z grafiku — systemu prawdy. Blok przecinający północ jest DZIELONY na doby lokalne (nocna zmiana
22–06 daje minuty w dwóch dniach), a część poza oknem tygodnia obcinana — żeby żadna minuta nie
wpadła do dwóch dni ani do obcego tygodnia.

DST: granice dób bierzemy w strefie ``tz`` (``datetime.combine``), ale MINUTY liczymy przez UTC.
Odejmowanie dwóch ``datetime`` o TYM SAMYM ``tzinfo`` (ZoneInfo) daje w Pythonie różnicę wall-clock,
która IGNORUJE DST — w dobie zmiany czasu zaniżyłoby to godziny. Konwersja do UTC przed odejmowaniem
daje REALNY czas (doba z cofnięciem zegara ma 25 h).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel

_UTC = timezone.utc


class ShiftBlock(BaseModel):
    """Opublikowany blok zmiany: kto i w jakim przedziale (świadomy ``datetime``, UTC z Graph).

    ``user_id`` to AAD user id — most do osoby przez ``IdentityDirectory.resolve_by_aad_user_id``.
    """

    user_id: str
    start: datetime
    end: datetime


class PersonDayMinutes(BaseModel):
    """Minuty przepracowane przez jedną osobę w jednej lokalnej dobie."""

    user_id: str
    day: date
    minutes: int


def minutes_by_person_day(
    blocks: list[ShiftBlock], *, since: date, until: date, tz: ZoneInfo
) -> list[PersonDayMinutes]:
    """Zsumuj minuty per (osoba, dzień) w oknie lokalnym PÓŁOTWARTYM ``[since, until)``.

    Każdy blok obcinamy do okna i DZIELIMY na doby lokalne. Rachunki w minutach; różnica świadomych
    ``datetime`` daje realny czas (odporność na DST). Wynik posortowany po (osoba, dzień).
    """
    window_start = datetime.combine(since, time.min, tzinfo=tz)
    window_end = datetime.combine(until, time.min, tzinfo=tz)
    totals: dict[tuple[str, date], int] = {}
    for block in blocks:
        start = max(block.start.astimezone(tz), window_start)
        end = min(block.end.astimezone(tz), window_end)
        cursor = start
        while cursor < end:
            day = cursor.date()
            next_midnight = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
            segment_end = min(end, next_midnight)
            # UTC przed odejmowaniem: różnica w tej samej strefie to wall-clock i gubi godzinę DST.
            elapsed = segment_end.astimezone(_UTC) - cursor.astimezone(_UTC)
            minutes = int(elapsed.total_seconds() // 60)
            if minutes > 0:
                totals[(block.user_id, day)] = totals.get((block.user_id, day), 0) + minutes
            cursor = segment_end
    return [
        PersonDayMinutes(user_id=user_id, day=day, minutes=minutes)
        for (user_id, day), minutes in sorted(totals.items())
    ]
