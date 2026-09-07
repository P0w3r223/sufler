"""Powody nieobecności jako słownictwo DOMENY: nazwa kanoniczna, normalizacja, powody zespołu.

Mieszkało to w ``reminders.timeoff``, a ``graph.client`` musiał stamtąd importować ``TeamReasons``
i ``normalize``, żeby zbudować odpowiedź z Graph. Powstawał przez to cykl na poziomie pakietów
(``graph`` → ``reminders`` → ``graph``), sprzeczny z deklaracją z README o acyklicznym kierunku
zależności. Nie wywracał niczego przy uruchomieniu — i właśnie dlatego nic go nie pilnowało.

Właściwe miejsce jest tutaj: to jest wiedza o dziedzinie (jak nazywa się nieobecność i po czym
poznać, że dwie nazwy znaczą to samo), a nie o Graphie ani o cyklu życia przypomnień. ``graph``
i ``reminders`` zależą teraz oba od ``domain``, w jedną stronę.

``reminders.timeoff`` re-eksportuje te nazwy, więc dotychczasowe importy nadal działają.
"""

from __future__ import annotations

from dataclasses import dataclass

# Kanoniczny powod z interpretera → nazwa wyświetlana powodu w Shifts (dopasowanie po displayName).
_CANONICAL_TO_DISPLAY = {
    "urlop": "Urlop",
    "nieobecność": "Nieobecność",
    "chorobowe": "Zwolnienie lekarskie",
    "urlop bezpłatny": "Urlop bezpłatny",
    "urlop rodzicielski": "Urlop rodzicielski",
}
_DEFAULT_DISPLAY = "Nieobecność"  # kanoniczna nazwa dla nierozpoznanego słowa
# Kolejność fallbacku, gdy konkretny powód nie istnieje w tenancie (generyczny, wciąż nieobecność).
_FALLBACK_DISPLAY = ("Nieobecność", "Urlop")


def normalize(name: str) -> str:
    """Znormalizuj nazwę powodu do porównań (małe litery, pojedyncze spacje)."""
    return " ".join(name.strip().lower().split())


def display_name(powod: str) -> str:
    """Powód z interpretera → kanoniczna nazwa (np. »chorobowe« → »Zwolnienie lekarskie«)."""
    return _CANONICAL_TO_DISPLAY.get(normalize(powod), _DEFAULT_DISPLAY)


@dataclass(frozen=True)
class TeamReasons:
    """Aktywne powody czasu wolnego zespołu: wyszukiwanie po nazwie + odwrotne po id."""

    by_name: dict[str, str]  # normalize(displayName) -> id
    names: dict[str, str]  # id -> displayName (oryginalna nazwa z Graph)

    def resolve(self, powod: str) -> tuple[str, str] | None:
        """(id, faktyczna nazwa) dla powodu; fallback do istniejącego generycznego.

        ``None`` tylko gdy zespół NIE MA żadnego aktywnego powodu (wtedy nie da się zapisać
        czasu wolnego wcale) — warstwa wyżej to sygnalizuje, nie gubi dnia po cichu.
        """
        for candidate in (display_name(powod), *_FALLBACK_DISPLAY):
            reason_id = self.by_name.get(normalize(candidate))
            if reason_id:
                return reason_id, self.names.get(reason_id, candidate)
        for reason_id, name in self.names.items():  # ostatecznie dowolny aktywny powód
            return reason_id, name
        return None
