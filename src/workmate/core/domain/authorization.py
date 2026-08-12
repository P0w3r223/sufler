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
    from workmate.core.domain.identity import Person


@dataclass(frozen=True)
class Actor:
    """Rozpoznany członek pionu — tożsamość zmapowana z konta Entra/AAD (B2 / ADR 0042).

    Minimalny value object: tyle, ile trzeba do decyzji i do czytelnego logu odmowy. Świadomie
    węższy niż ``Person`` (który niesie też konto Jiry, ADR 0054) — autoryzacja notatki potrzebuje
    wyłącznie „kto to jest", nie mapowań na inne systemy.
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


def can_read_note(actor: Actor | None, *, project: str | None = None) -> bool:
    """Czy ``actor`` może CZYTAĆ bazę wiedzy? Bramka członkostwa: rozpoznany członek → tak;
    ``None`` → nie (fail-closed, ADR 0062).

    Symetria do ``can_write_meeting_note`` — ta sama polityka B2-A, ale ODDZIELNA funkcja, żeby
    odczyt i zapis mogły się rozejść później (np. zawężenie odczytu per-projekt przy zapisie wciąż
    członkostwem) bez ruszania miejsc wywołań. ``project`` jest tu jako TEN SAM SZEW pod politykę
    per-projekt/firma: dziś rozpoznany członek czyta CAŁĄ bazę (zachowuje kulturę cross-team, na
    której stoi prompt), a szew czeka na osobny ADR (ADR 0062 §Alternatywy / Follow-ups).
    """
    _ = project  # szew pod politykę per-projekt; bramka członkostwa go nie różnicuje
    return actor is not None


def can_use_shell(actor: Actor | None) -> bool:
    """Czy ``actor`` może dostać narzędzie POWŁOKI (``Bash``, ADR 0057)? Bramka członkostwa:
    rozpoznany członek → tak; ``None`` → nie (fail-closed, ADR 0063).

    Ta sama polityka B2-A co odczyt/zapis, ale ODDZIELNA funkcja — z tego samego powodu, dla którego
    ``can_read_note`` jest osobna od ``can_write_meeting_note``: polityki mogą się rozejść później
    bez ruszania miejsc wywołań. Powłoka czyta CAŁY wolumen brudnopisu i montaż ``ro`` bazy wiedzy
    ścieżką bezwzględną (nie jest zamknięta w scope rozmowy — ADR 0057), więc jej granica zaufania
    MUSI zrównać się z granicą pozostałych ścieżek danych (``identities.yaml``), a nie z luźniejszym
    udziałem w kanale. Bez ``project`` — powłoka nie jest per-projekt (dowolny kod, nie akcja na
    wskazanym projekcie); domknięcie cross-read MIĘDZY członkami to osobna warstwa (montaż
    per-rozmowa, ADR 0063 §2 / infra ADR 0010), nie ta decyzja.
    """
    return actor is not None
