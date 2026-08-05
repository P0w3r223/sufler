"""Wykrywanie osób bez zmian na wskazany tydzień (czysta logika)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, tzinfo

from powiadomienia_teams.domain.models import Member, Shift, TimeOff

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
        days = {
            d
            for d in range(7)
            if t.start < (target_monday + timedelta(days=d + 1))
            and t.end > (target_monday + timedelta(days=d))
        }
        if days:
            result.setdefault(t.user_id, set()).update(days)
    return {uid: frozenset(days) for uid, days in result.items()}


def member_filled_week(member_id: str, shifts: Iterable[Shift], off_days: frozenset[int]) -> bool:
    """Czy dana osoba MA już grafik na docelowy tydzień: jakakolwiek zmiana albo pełny pn–pt urlop.

    Odwrotność kryterium z ``members_without_shifts`` (patrz tam), zawężona do JEDNEJ osoby — dzięki
    temu decyzja „przestań nagabywać, bo już uzupełnił" jest symetryczna z decyzją „zacznij pytać".
    ``shifts`` są zawężone wcześniej do docelowego tygodnia; ``off_days`` to dni tej osoby pokryte
    urlopem (z ``off_weekdays_by_member``).
    """
    return any(s.user_id == member_id for s in shifts) or off_days >= _WORKING_WEEK


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
    covered_by_shift = {s.user_id for s in shifts}
    covered_by_full_off = {uid for uid, days in off_by_member.items() if days >= _WORKING_WEEK}
    covered = covered_by_shift | covered_by_full_off
    return [m for m in members if m.user_id not in covered]
