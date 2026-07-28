"""Autoryzacja zapisu notatki ze spotkania (B2 / ADR 0042) — czysta decyzja rdzenia.

Kto może złożyć notatkę komendą ``/notatka`` z drzwi Teams? Tożsamość nadawcy przychodzi jako
``aad_user_id`` (Entra), a rdzeń rozstrzyga to na ROZWIĄZANYM aktorze — nigdy na surowym id +
katalogu. Rozwiązanie tożsamości (Graph/YAML) żyje w adapterze za portem ``AadIdentityLookup``;
tutaj jest wyłącznie POLITYKA, jako czysta funkcja bez I/O (testowalna wprost).

B2-A to bramka członkostwa: rozpoznany członek (wg mapy tożsamości) → wolno; nieznany (``None``) →
nie (fail-closed). „Rozpoznany" znaczy tyle, ile gwarantuje wpięty katalog: wariant plikowy
(``YamlIdentityDirectory``) potwierdza OBECNOŚĆ we wpisach, a wariant Graph
(``GraphIdentityDirectory``, drop-in pod tym samym portem) dokłada AKTUALNE członkostwo zespołu.
``can_write_meeting_note`` już
przyjmuje ``project`` jako SZEW pod politykę per-projekt/firma (B2-B, ADR 0042 §Alternatywy) — dziś
bramka członkostwa go nie różnicuje, ale wołający już go podaje, więc macierz uprawnień dołoży się
bez zmiany wywołań.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workmate.core.domain.timesheet import Person


@dataclass(frozen=True)
class Actor:
    """Rozpoznany członek pionu — tożsamość zmapowana z konta Entra/AAD (B2 / ADR 0042).

    Minimalny value object: tyle, ile trzeba do decyzji i do czytelnego logu odmowy. Świadomie
    węższy niż ``Person`` (który niesie identyfikatory Jiry/git do worklogów) — autoryzacja
    notatki potrzebuje wyłącznie „kto to jest", nie mapowań na inne systemy.
    """

    aad_user_id: str
    display_name: str


def actor_from_person(person: Person) -> Actor:
    """Zmapuj rozwiązaną ``Person`` (z katalogu tożsamości) na ``Actor`` autoryzacji."""
    return Actor(aad_user_id=person.aad_user_id, display_name=person.display_name)


def can_write_meeting_note(actor: Actor | None, *, project: str) -> bool:
    """Czy ``actor`` może złożyć notatkę? B2-A: rozpoznany członek → tak; ``None`` → nie.

    ``project`` jest w sygnaturze pod POLITYKĘ per-projekt (B2-B): dziś bramka członkostwa nie
    różnicuje po projekcie (każdy członek może pisać do każdego projektu), ale wołający już go
    podaje, więc dołożenie macierzy uprawnień nie zmieni miejsc wywołań (ADR 0042).
    """
    _ = project  # szew pod B2-B; bramka członkostwa (B2-A) nie różnicuje po projekcie
    return actor is not None
