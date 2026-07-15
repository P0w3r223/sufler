"""Model domenowy grafiku zmian (Microsoft Shifts).

Czyste, niemutowalne struktury bez I/O. Logika przypomnień operuje na tych typach,
a warstwa Graph tłumaczy JSON ↔ te modele. Wszystkie czasy są tz-aware (UTC).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

_MAX_SHIFT = timedelta(hours=24)


class InvalidShift(ValueError):
    """Zmiana narusza kontrakt domenowy (naiwny czas, zła kolejność lub absurdalna długość)."""


@dataclass(frozen=True)
class Member:
    """Członek zespołu — kandydat do powiadomienia."""

    user_id: str
    display_name: str
    email: str | None = None
    roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.user_id or not self.display_name:
            raise ValueError("Member wymaga niepustych user_id i display_name")


@dataclass(frozen=True)
class Shift:
    """Pojedyncza zmiana (odczytana z Shifts lub proponowana do zapisu).

    `start`/`end` muszą być tz-aware (przechowujemy w UTC). `shared=True` to zmiana
    opublikowana (widoczna dla pracownika); `False` = wersja robocza (draft).
    """

    user_id: str
    start: datetime
    end: datetime
    shared: bool = True
    scheduling_group_id: str | None = None
    theme: str | None = None  # kolor Shifts = tryb pracy (blue=zdalnie, green=stacjonarnie)

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise InvalidShift("Shift.start/end muszą być tz-aware (UTC)")
        if self.end <= self.start:
            raise InvalidShift(
                f"Koniec {self.end.isoformat()} nie jest po początku {self.start.isoformat()}"
            )
        if self.end - self.start > _MAX_SHIFT:
            raise InvalidShift(
                f"Zmiana dłuższa niż 24h: {self.start.isoformat()}–{self.end.isoformat()}"
            )


@dataclass(frozen=True)
class WeekSchedule:
    """Komplet zmian jednej osoby na dany tydzień (np. propozycja »jak ostatnio«)."""

    member_id: str
    week_start: date  # poniedziałek (lokalnie)
    shifts: tuple[Shift, ...] = ()

    def __post_init__(self) -> None:
        if self.week_start.weekday() != 0:
            raise ValueError(
                f"week_start musi być poniedziałkiem, jest {self.week_start.isoformat()}"
            )

    @property
    def is_empty(self) -> bool:
        return not self.shifts
