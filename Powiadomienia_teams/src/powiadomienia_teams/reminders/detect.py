"""Wykrywanie osób bez zmian na wskazany tydzień (czysta logika)."""
from __future__ import annotations

from collections.abc import Iterable

from powiadomienia_teams.domain.models import Member, Shift, TimeOff


def members_without_shifts(
    members: Iterable[Member],
    shifts: Iterable[Shift],
    time_offs: Iterable[TimeOff] = (),
) -> list[Member]:
    """Zwróć członków, którzy nie mają ŻADNEJ zmiany ANI czasu wolnego w docelowym tygodniu.

    `shifts` i `time_offs` są zawężone wcześniej do docelowego tygodnia. Kolejność wyniku =
    kolejność `members` (deterministyczna).

    Zatwierdzony urlop LICZY SIĘ jako uzupełniony grafik: osoba na urlopie świadomie nie ma zmian,
    więc prośba o uzupełnienie byłaby nagabywaniem, a jej odpowiedź („cały tydzień urlop")
    stworzyłaby DRUGI komplet wpisów timeOff na te same dni — `create_time_off` nie deduplikuje.
    """
    covered = {s.user_id for s in shifts} | {t.user_id for t in time_offs}
    return [m for m in members if m.user_id not in covered]
