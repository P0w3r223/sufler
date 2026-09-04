"""Strażniki bezpieczeństwa zapisu (czysta logika, bez I/O).

Wydzielone z orkiestracji, bo cross-user tripwire to pierwszorzędna granica bezpieczeństwa —
powinna być jawnym, testowalnym modułem, a nie ukrytą funkcją w pliku obiegu.
"""
from __future__ import annotations

from collections.abc import Iterable

from powiadomienia_teams.domain.models import TimeOff, WeekSchedule


class CrossUserWriteError(RuntimeError):
    """Próba zapisu zmiany dla innego pracownika niż adresat przypomnienia."""


def ensure_single_owner(
    member_id: str, schedule: WeekSchedule, time_offs: Iterable[TimeOff] = ()
) -> None:
    """Twarda granica: KAŻDY zapis (zmiana i czas wolny) musi należeć do adresata (`member_id`).

    Odpowiedź pracownika steruje wyłącznie dniami/godzinami i wolnym WŁASNEGO grafiku — nigdy
    tożsamością osoby (schemat wyjścia modelu nie ma pola użytkownika). Ten warunek egzekwuje
    ten niezmiennik na granicy nieodwracalnego zapisu, nawet gdyby przyszły refaktor przypadkiem
    przepuścił cudze `user_id`. Nie da się więc czyjąkolwiek odpowiedzią wpisać nic innej osobie.
    """
    foreign = sorted(
        {s.user_id for s in schedule.shifts if s.user_id != member_id}
        | {t.user_id for t in time_offs if t.user_id != member_id}
    )
    if foreign:
        raise CrossUserWriteError(
            f"Zapis odrzucony: wpisy dla {foreign} ≠ adresat {member_id!r}"
        )
