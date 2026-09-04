"""Wykrywanie osób bez zmian na wskazany tydzień (czysta logika)."""
from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from datetime import datetime, timedelta, tzinfo

from powiadomienia_teams.domain.models import Member, Shift, TimeOff
from powiadomienia_teams.domain.tozsamosc import ten_sam, znormalizuj

# Tydzień roboczy = poniedziałek–piątek. Tylko pełne pokrycie tych dni urlopem uznajemy za
# „nie ma o co pytać"; urlop w części dni NIE wycisza prośby o pozostałe dni.
_WORKING_WEEK = frozenset(range(5))  # pon–pt


def off_weekdays_by_member(
    time_offs: Iterable[TimeOff],
    target_monday: datetime,
    tz: tzinfo,
) -> dict[str, frozenset[int]]:
    """`user_id` → zbiór weekdayów (0–6) docelowego tygodnia pokrytych urlopem tej osoby.

    `target_monday` to LOKALNA północ poniedziałku celu (tz-aware), `tz` = strefa zespołu.
    Dzień uznajemy za wolny, gdy urlop ``[start, end)`` przecina lokalny dobowy blok tego dnia —
    dzięki temu urlop wielodniowy/wielotygodniowy mapuje się na właściwe dni bieżącego tygodnia.
    `tz` przyjmujemy jawnie, choć granice dni bierzemy z `target_monday`, by intencja (dni liczone
    w strefie zespołu) była czytelna w miejscu wywołania.
    """
    del tz  # granice dni pochodzą z tz-aware `target_monday`; parametr dokumentuje strefę
    result: dict[str, set[int]] = {}
    for t in time_offs:
        days = _dni_pokryte(t, target_monday)
        if days:
            # Klucz ZNORMALIZOWANY: mapa jest odczytywana przez `member_id` z pliku stanu,
            # a `t.user_id` przychodzi z `read_time_off` — to dwa różne wywołania Graph, więc
            # dwie różne pisownie tego samego GUID-a. Rozjazd dawał puste pokrycie, a puste
            # pokrycie znaczy „nic jeszcze nie zapisano" i kończy się DRUGIM kompletem zmian
            # w grafiku klienta. Wartości (`user_id` w obiektach) zostają surowe.
            result.setdefault(znormalizuj(t.user_id), set()).update(days)
    return {uid: frozenset(days) for uid, days in result.items()}


def _dni_pokryte(time_off: TimeOff, target_monday: datetime) -> set[int]:
    """Weekdaye (0–6) docelowego tygodnia, które ten wpis czasu wolnego przecina.

    Wydzielone, bo definicję „dzień jest wolny" czytają dziś DWIE ścieżki o różnych celach:
    mapa pokrycia dla runtime (``off_weekdays_by_member``) i etykieta powodu dla narzędzia modelu
    (``off_reason_by_weekday``). Dwie kopie tego warunku rozjechałyby się przy pierwszej zmianie
    definicji doby, a rozjazd znaczyłby, że model widzi inny zbiór dni wolnych niż ten, którym
    zapis odsiewa dni już pokryte — czyli propozycję dublującą istniejący wpis.
    """
    return {
        d
        for d in range(7)
        if time_off.start < (target_monday + timedelta(days=d + 1))
        and time_off.end > (target_monday + timedelta(days=d))
    }


def off_reason_by_weekday(
    time_offs: Iterable[TimeOff], member_id: str, target_monday: datetime
) -> dict[int, str]:
    """Weekday (0–6) → ``reason_id`` czasu wolnego TEJ osoby w docelowym tygodniu.

    Pierwszy wpis pokrywający dany dzień wygrywa — tak samo jak dotąd robił to czytnik narzędzia.
    Nakładające się urlopy o różnych powodach są w Shifts możliwe, ale dzień może mieć w wyniku
    jedną etykietę, a wybór „pierwszy z odczytu" jest przynajmniej stabilny wobec kolejności
    zwracanej przez Graph. Rozstrzygnięcie ``reason_id`` na NAZWĘ należy do wołającego: ta funkcja
    zostaje czysta i nie potrzebuje ``TeamReasons``.
    """
    dni: dict[int, str] = {}
    for t in time_offs:
        if not ten_sam(t.user_id, member_id):
            continue
        for d in _dni_pokryte(t, target_monday):
            dni.setdefault(d, t.reason_id)
    return dni


def member_filled_week(
    member_id: str, shifts: Iterable[Shift], off_days: frozenset[int]
) -> bool:
    """Czy dana osoba MA już grafik na docelowy tydzień: jakakolwiek zmiana albo pełny pn–pt urlop.

    Odwrotność kryterium z ``members_without_shifts`` (patrz tam), zawężona do JEDNEJ osoby — dzięki
    temu decyzja „przestań nagabywać, bo już uzupełnił" jest symetryczna z decyzją „zacznij pytać".
    ``shifts`` są zawężone wcześniej do docelowego tygodnia; ``off_days`` to dni tej osoby pokryte
    urlopem (z ``off_weekdays_by_member``).
    """
    return any(ten_sam(s.user_id, member_id) for s in shifts) or _WORKING_WEEK <= off_days


def shift_weekdays(member_id: str, shifts: Iterable[Shift], tz: tzinfo) -> frozenset[int]:
    """Dni (0=pon…6=nd), w których ta osoba MA już zmianę w odczytanym oknie.

    Dzień liczony jest ROZPOCZĘCIEM zmiany — zgodnie z niezmiennikiem dnia startu, który obowiązuje
    w całym projekcie (nocka piątek→sobota jest zmianą piątkową; patrz ``interpreter.build_schedule``).
    """
    return frozenset(
        s.start.astimezone(tz).weekday() for s in shifts if ten_sam(s.user_id, member_id)
    )


def drop_already_covered(
    shifts: Iterable[Shift],
    time_offs: Iterable[TimeOff],
    *,
    covered: Collection[int],
    tz: tzinfo,
) -> tuple[tuple[Shift, ...], tuple[TimeOff, ...], tuple[int, ...]]:
    """Odsiej wpisy dotyczące dni, które w grafiku SĄ JUŻ pokryte. Zwraca (zmiany, wolne, odsiane dni).

    Powód istnienia jest wąski i konkretny: między prośbą o uzupełnienie a „tak" pracownika mijają
    dni (piątkowy przebieg → termin odpowiedzi w poniedziałek nad ranem), a w tym czasie grafik
    mógł uzupełnić przełożony albo sam
    pracownik w Shifts. ``create_shift`` i ``create_time_off`` NIE deduplikują, więc zapis bez tego
    filtra dokłada DRUGI komplet wpisów — dokładnie ta nieodwracalna szkoda, przed którą broni się
    cała reszta obiegu (``GraphTruncatedReadError``, ``ensure_single_owner``, semantyka
    „co najwyżej raz").

    Jeden wspólny zbiór ``covered`` dla zmian i dla czasu wolnego jest celowy: dzień oznaczony już
    jako wolny nie może dostać pracy, a dzień z istniejącą zmianą nie może dostać urlopu — obie
    kombinacje zostawiłyby w grafiku wpisy przeczące sobie. Kolizji nie rozstrzygamy po cichu: dzień
    trafia do wyniku jako odsiany i wołający ma obowiązek powiedzieć o nim pracownikowi.
    """
    zajete = frozenset(covered)
    zmiany = tuple(s for s in shifts if s.start.astimezone(tz).weekday() not in zajete)
    wolne = tuple(t for t in time_offs if t.start.astimezone(tz).weekday() not in zajete)
    odsiane = {s.start.astimezone(tz).weekday() for s in shifts} | {
        t.start.astimezone(tz).weekday() for t in time_offs
    }
    return zmiany, wolne, tuple(sorted(odsiane & zajete))


def members_without_shifts(
    members: Iterable[Member],
    shifts: Iterable[Shift],
    off_by_member: Mapping[str, frozenset[int]] | None = None,
) -> list[Member]:
    """Zwróć członków, do których należy napisać o uzupełnienie grafiku na docelowy tydzień.

    Pomijamy osobę, która ma JAKĄKOLWIEK zmianę w docelowym tygodniu (grafik zaczęty — nie
    nagabujemy), albo której urlop pokrywa CAŁY tydzień roboczy (pon–pt): wtedy nie ma o co pytać.

    Urlop CZĘŚCIOWY (np. tylko piątek) NIE wycisza już prośby — osoba trafia na listę, a wołający
    wyklucza dni wolne z propozycji i wspomina o nich w treści (patrz `propose.skip_weekdays`
    i `messages.build_nudge_text`). `off_by_member` to gotowa mapa dni z `off_weekdays_by_member`.

    `shifts` są zawężone wcześniej do docelowego tygodnia. Kolejność wyniku = kolejność `members`
    (deterministyczna).
    """
    off_by_member = off_by_member or {}
    covered_by_shift = {znormalizuj(s.user_id) for s in shifts}
    covered_by_full_off = {
        uid for uid, days in off_by_member.items() if _WORKING_WEEK <= days
    }
    covered = covered_by_shift | covered_by_full_off
    return [m for m in members if znormalizuj(m.user_id) not in covered]
