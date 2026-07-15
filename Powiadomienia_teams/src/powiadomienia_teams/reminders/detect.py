"""Wykrywanie osób bez zmian na wskazany tydzień (czysta logika)."""
from __future__ import annotations

from collections.abc import Iterable

from powiadomienia_teams.domain.models import Member, Shift


def members_without_shifts(members: Iterable[Member], shifts: Iterable[Shift]) -> list[Member]:
    """Zwróć członków, którzy nie mają ŻADNEJ zmiany w `shifts`.

    `shifts` to zmiany zawężone wcześniej do docelowego tygodnia. Kolejność wyniku =
    kolejność `members` (deterministyczna).
    """
    covered = {s.user_id for s in shifts}
    return [m for m in members if m.user_id not in covered]
