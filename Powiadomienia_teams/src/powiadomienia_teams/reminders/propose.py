"""Budowa propozycji grafiku: typowy tydzień z ostatnich tygodni (czysta logika)."""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from powiadomienia_teams.domain.models import Shift, TimeOff, WeekSchedule

#: Ile tygodni wstecz liczy się do propozycji („średnia z ostatniego miesiąca").
TYGODNIE_HISTORII = 4
#: Godziny wyliczone medianą są zaokrąglane do tej siatki — w grafiku nikt nie pracuje od 7:52.
_SIATKA_MIN = 15


def baza_interpretacji(
    proposal: list[dict[str, Any]],
    resolved: list[dict[str, Any]],
    resolved_time_off: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Grafik pod KOLEJNĄ poprawkę pracownika: ustalony, a gdy go nie ma — gotowiec.

    Semantyka w jednym zdaniu: to grafik, który zapiszemy, jeśli pracownik nie poprosi o zmianę.
    Na początku rozmowy jest nim gotowiec »jak w zeszłym tygodniu«, po pierwszej poprawce — to,
    co już uzgodniono.

    Bez tego rozróżnienia każda kolejna poprawka była nanoszona na ORYGINAŁ, a pierwsza przeżywała
    wyłącznie dzięki pamięci rozmowy, która ma twarde okno godziny liczone od pierwszej wiadomości
    (``replies.MEMORY_WINDOW``). Po jego upływie pracownik poprawiający środę, a potem czwartek,
    dostawał do potwierdzenia tydzień bez środy — i nie miał powodu podejrzewać, że coś zniknęło.
    Rozmowa dłuższa niż godzina zamieniała się w pętlę.

    Czysta i celowo trywialna: wartość tej funkcji leży w NAZWIE i w tym, że jest jedno miejsce,
    które o tym rozstrzyga, a nie w obliczeniu.

    ``resolved_time_off`` NIE wchodzi do wyniku — bazą są dni PRACUJĄCE. Rozstrzyga natomiast
    o fallbacku, bo sama pusta lista zmian nie odróżnia „nic nie uzgodniono" od „uzgodniono, że
    pracownik ma wolne przez cały tydzień". Bez tego rozróżnienia odpowiedź „biorę urlop na cały
    tydzień", a potem „a w piątek jednak przyjdę" wracała do grafiku z zeszłego tygodnia i cicho
    przywracała cztery dni pracy, których pracownik już nie chciał.
    """
    if resolved or resolved_time_off:
        return resolved
    return proposal


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


@dataclass(frozen=True)
class Propozycja:
    """Propozycja grafiku wraz z tym, z czego powstała — do treści wiadomości i do logu.

    ``tygodnie_uzyte`` to tygodnie z JAKIMKOLWIEK wpisem (zmiana albo czas wolny). Tygodnie bez
    żadnego wpisu nie są dowodem, że ktoś nie pracował — najczęściej nikt ich nie uzupełnił, a to
    właśnie jest problem, który ta usługa rozwiązuje. ``tygodnie_z_urlopem`` liczy tygodnie, w
    których choć jeden dzień był wolny; te dni wypadają z liczenia, reszta tygodnia zostaje.
    """

    grafik: WeekSchedule
    tygodnie_uzyte: int = 0
    tygodnie_z_urlopem: int = 0
    tygodnie_bez_wpisow: int = 0

    @property
    def opis_podstawy(self) -> str:
        """Krótkie zdanie „skąd ta propozycja" — pusty napis, gdy nie ma z czego jej zbudować."""
        if self.grafik.is_empty or not self.tygodnie_uzyte:
            return ""
        if self.tygodnie_uzyte == 1:
            # Jeden tydzień to nie „typowy grafik" — mówimy wprost, skąd propozycja się wzięła.
            opis = "na podstawie ostatniego tygodnia, w którym masz grafik"
        else:
            opis = f"typowy grafik z {self.tygodnie_uzyte} ostatnich tygodni"
        if self.tygodnie_z_urlopem:
            opis += " (dni urlopu pominąłem)"
        return opis


def _lokalnie(dzien: date, minuty: int, tz: ZoneInfo) -> datetime:
    """Lokalna chwila ``dzien`` + ``minuty`` od północy, w UTC. Liczone na ścianie zegara (DST)."""
    naiwna = datetime.combine(dzien, time(0)) + timedelta(minutes=minuty)
    return naiwna.replace(tzinfo=tz).astimezone(timezone.utc)


def _minuty(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def _do_siatki(minuty: float) -> int:
    return int(round(minuty / _SIATKA_MIN) * _SIATKA_MIN)


def _dni_wolne(
    member_id: str, time_off: Iterable[TimeOff], tz: ZoneInfo, *, od: date, do: date
) -> set[date]:
    """Lokalne daty z przedziału ``[od, do)`` objęte czasem wolnym danej osoby."""
    wolne: set[date] = set()
    for t in time_off:
        if t.user_id != member_id:
            continue
        dzien = max(t.start.astimezone(tz).date(), od)
        koniec = t.end.astimezone(tz)
        # Wpis kończący się równo o północy nie obejmuje dnia, który się wtedy zaczyna.
        ostatni = (koniec - timedelta(microseconds=1)).date()
        while dzien <= ostatni and dzien < do:
            wolne.add(dzien)
            dzien += timedelta(days=1)
    return wolne


def _typowy_dzien(
    dni: Sequence[tuple[Shift, ...]], tz: ZoneInfo
) -> tuple[tuple[tuple[int, int], ...], str | None, str | None]:
    """Godziny, tryb i grupa typowego dnia z listy dni PRACUJĄCYCH, od najnowszego.

    Godziny wybieramy w trzech krokach, od najbardziej do najmniej dosłownego:

    1. **Grafik, który się POWTARZA** (ten sam zestaw godzin co najmniej dwa razy) — wygrywa
       najczęstszy, a przy remisie najnowszy. To jest przypadek typowy i daje godziny, które
       ktoś naprawdę przepracował; średnia z „8–16, 8–16, 8–16, 10–14" dałaby 8:30–15:30,
       czyli grafik, którego nie było ani razu.
    2. **Brak powtórzeń, jedna zmiana dziennie** — mediana początku i mediana długości,
       zaokrąglone do kwadransa. Mediana, nie średnia arytmetyczna: jeden krótszy dzień nie
       przesuwa całego tygodnia.
    3. **Brak powtórzeń i dzielone zmiany** — najnowszy dzień w całości. Uśrednianie dwóch
       różnych podziałów dnia nie ma sensownego wyniku.
    """
    wzorce = [
        tuple(
            (_minuty(s.start.astimezone(tz)), int((s.end - s.start).total_seconds() // 60))
            for s in dzien
        )
        for dzien in dni
    ]
    licznik = Counter(wzorce)
    najczestszy, ile = max(licznik.items(), key=lambda kv: (kv[1], -wzorce.index(kv[0])))
    if ile >= 2:
        godziny = najczestszy
    elif all(len(w) == 1 for w in wzorce):
        start = _do_siatki(statistics.median(w[0][0] for w in wzorce))
        dlugosc = max(_SIATKA_MIN, _do_siatki(statistics.median(w[0][1] for w in wzorce)))
        godziny = ((start, dlugosc),)
    else:
        godziny = wzorce[0]
    motywy = [dzien[0].theme for dzien in dni]
    motyw_licznik = Counter(motywy)
    motyw = max(motyw_licznik, key=lambda m: (motyw_licznik[m], -motywy.index(m)))
    return godziny, motyw, dni[0][0].scheduling_group_id


def proposal_from_history(
    member_id: str,
    history_shifts: Iterable[Shift],
    history_time_off: Iterable[TimeOff],
    target_week_start: date,
    *,
    tz: ZoneInfo,
    skip_weekdays: frozenset[int] = frozenset(),
    tygodnie: int = TYGODNIE_HISTORII,
) -> Propozycja:
    """Typowy tydzień pracy z ``tygodnie`` ostatnich tygodni przed ``target_week_start``.

    Zasady — każda wynika z tego, że pracownik ZWYKLE pracuje, a dni wolne są wyjątkiem:

    - **Urlop nie głosuje.** Dzień objęty czasem wolnym (i bez zmiany) wypada z liczenia dla
      swojego dnia tygodnia — nie liczy się ani jako praca, ani jako jej brak. Tydzień urlopu nie
      robi więc z propozycji pustego tygodnia, a pojedynczy wolny piątek nie zabiera piątku.
    - **Tydzień bez żadnego wpisu nie głosuje.** Nie wiadomo, czy ktoś wtedy nie pracował, czy
      po prostu nikt nie uzupełnił grafiku.
    - **Dzień wchodzi do propozycji, gdy był pracujący w co najmniej połowie tygodni**, w których
      o tym dniu cokolwiek wiadomo. Remis rozstrzyga się na korzyść pracy: okres mniejszej
      aktywności (np. dwa tygodnie po trzy dni) nie kasuje od razu reszty tygodnia, a pracownik
      i tak poprawia propozycję jednym zdaniem. Pracuje ktoś stale mniej — po kilku tygodniach
      propozycja sama się do tego dostosuje.
    - Godziny, tryb pracy i grupa — patrz ``_typowy_dzien``.

    ``skip_weekdays`` to dni wolne już wpisane w tygodniu DOCELOWYM — w nie pracy nie proponujemy.
    Przy jednym tygodniu historii wynik jest tożsamy z dawnym „jak w zeszłym tygodniu".
    """
    poczatki = [target_week_start - timedelta(weeks=k) for k in range(1, tygodnie + 1)]
    od, do = poczatki[-1], target_week_start
    wolne = _dni_wolne(member_id, history_time_off, tz, od=od, do=do)
    zmiany_dnia: dict[date, list[Shift]] = {}
    for s in history_shifts:
        if s.user_id != member_id:
            continue
        dzien = s.start.astimezone(tz).date()
        if od <= dzien < do:
            zmiany_dnia.setdefault(dzien, []).append(s)

    uzyte: list[date] = []
    z_urlopem = bez_wpisow = 0
    for poczatek in poczatki:  # od najnowszego
        dni = [poczatek + timedelta(days=d) for d in range(7)]
        if not any(d in zmiany_dnia or d in wolne for d in dni):
            bez_wpisow += 1
            continue
        uzyte.append(poczatek)
        if any(d in wolne and d not in zmiany_dnia for d in dni):
            z_urlopem += 1

    nowe: list[Shift] = []
    for wd in range(7):
        if wd in skip_weekdays:
            continue
        pracujace: list[tuple[Shift, ...]] = []
        znane = 0
        for poczatek in uzyte:
            dzien = poczatek + timedelta(days=wd)
            if dzien in zmiany_dnia:
                znane += 1
                pracujace.append(tuple(sorted(zmiany_dnia[dzien], key=lambda s: s.start)))
            elif dzien not in wolne:
                znane += 1
        if not pracujace or 2 * len(pracujace) < znane:
            continue
        godziny, motyw, grupa = _typowy_dzien(pracujace, tz)
        cel = target_week_start + timedelta(days=wd)
        for start_min, dlugosc in godziny:
            start = _lokalnie(cel, start_min, tz)
            nowe.append(
                Shift(
                    user_id=member_id,
                    start=start,
                    end=_lokalnie(cel, start_min + dlugosc, tz),
                    scheduling_group_id=grupa,
                    theme=motyw,
                )
            )
    grafik = WeekSchedule(member_id=member_id, week_start=target_week_start, shifts=tuple(nowe))
    return Propozycja(grafik, len(uzyte), z_urlopem, bez_wpisow)
