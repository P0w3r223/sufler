"""Model domenowy grafiku zmian (Microsoft Shifts).

Czyste, niemutowalne struktury bez I/O. Logika przypomnień operuje na tych typach,
a warstwa Graph tłumaczy JSON ↔ te modele. Wszystkie czasy są tz-aware (UTC).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import NamedTuple

_MAX_SHIFT = timedelta(hours=24)


class InvalidShift(ValueError):
    """Zmiana narusza kontrakt domenowy (naiwny czas, zła kolejność lub absurdalna długość)."""


class InvalidTimeOff(ValueError):
    """Wpis czasu wolnego narusza kontrakt domenowy (naiwny czas lub zła kolejność)."""


@dataclass(frozen=True)
class Member:
    """Członek zespołu — kandydat do powiadomienia."""

    user_id: str
    # Poza `repr` — patrz `state.PendingReminder.member_name` (N28).
    display_name: str = field(repr=False)
    email: str | None = field(default=None, repr=False)
    roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.user_id or not self.display_name:
            raise ValueError("Member wymaga niepustych user_id i display_name")


@dataclass(frozen=True)
class Shift:
    """Pojedyncza zmiana (odczytana z Shifts lub proponowana do zapisu).

    `start`/`end` muszą być tz-aware (przechowujemy w UTC).

    Zmiana MOŻE przechodzić przez północ (nocka 22:00–06:00) — wtedy `end` wypada następnego dnia
    kalendarzowego, a zmiana należy do dnia, w którym się ZACZYNA. Ten niezmiennik obowiązuje
    w całym projekcie; buduje go `agent.interpreter.build_schedule` (patrz tam) i zakładają go
    okienkowanie odczytu, wykrywanie luk oraz propozycja »jak w zeszłym tygodniu«.
    """

    user_id: str
    start: datetime
    end: datetime
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
class TimeOff:
    """Wpis czasu wolnego (urlop, nieobecność, zwolnienie) do zapisu w Shifts.

    `start`/`end` tz-aware (UTC). `reason_id` to id powodu Shifts (``TOR_…``), rozstrzygane
    wcześniej z listy `timeOffReasons` zespołu. Dzień wolny modelujemy jako całodobowy blok
    (lokalna północ–północ), dlatego — inaczej niż `Shift` — nie ma limitu 24h.
    """

    user_id: str
    start: datetime
    end: datetime
    reason_id: str

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise InvalidTimeOff("TimeOff.start/end muszą być tz-aware (UTC)")
        if self.end <= self.start:
            raise InvalidTimeOff(
                f"Koniec {self.end.isoformat()} nie jest po początku {self.start.isoformat()}"
            )
        if not self.reason_id:
            raise InvalidTimeOff("TimeOff wymaga niepustego reason_id")


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


class DaneTygodnia(NamedTuple):
    """Wszystko, co jeden przebieg wie o grafiku zespołu w JEDNYM tygodniu.

    Wypełnia to ``runtime.snapshot.SnapshotGrafiku``, ale TYP mieszka tutaj, bo czytają go dwie
    warstwy naraz: ``runtime`` (świeżość przed zapisem, wykrywanie samouzupełnienia) i ``agent``
    (czytnik narzędzia modelu). Gdyby typ został w ``runtime``, ``agent`` musiałby go stamtąd
    zaimportować — a ``runtime`` importuje ``agent``, więc powstałby cykl między pakietami.
    Domena jest miejscem na wspólne słownictwo dokładnie dla takich przypadków.

    ``NamedTuple``, a nie zwykła krotka, od chwili gdy pól zrobiło się więcej niż dwa: rozpakowanie
    pozycyjne ``a, b = dane`` przy trzecim polu pada głośno, ale ``dane[0]``/``dane[1]`` u drugiego
    konsumenta milczy i podaje co innego, niż nazwa sugeruje.

    ``wolne_dni`` (mapa dni per osoba) i ``wolne`` (surowe wpisy) NIE są duplikatem: pierwsze
    odpowiada na „czy ten dzień jest już pokryty", drugie niesie ``reason_id``, bez którego nie da
    się powiedzieć, JAKI to czas wolny. Mapa liczy się raz, przy pobraniu, zamiast u każdego
    wołającego osobno.

    ``poniedzialek`` to lokalna północ poniedziałku tego tygodnia — JEDYNE miejsce, w którym ta
    granica powstaje. Wcześniej liczyły ją dwa moduły osobno (snapshot i czytnik narzędzia), a dwa
    wyliczenia tej samej granicy rozjeżdżają się przy pierwszej zmianie definicji doby.
    """

    zmiany: tuple[Shift, ...]
    wolne_dni: dict[str, frozenset[int]]
    wolne: tuple[TimeOff, ...]
    poniedzialek: datetime
