"""Powody nieobecności (język pracownika) → powód Shifts (timeOffReason).

Czysta logika. Interpreter zwraca kanoniczny `powod` z wąskiej listy; tutaj tłumaczymy go na
nazwę wyświetlaną w Shifts, a `TeamReasons` (zbudowane z żywych powodów zespołu) rozstrzyga go na
konkretne id (``TOR_…``) i FAKTYCZNĄ nazwę wyświetlaną — z fallbackiem do istniejącego powodu,
żeby dzień wolny nigdy nie zginął po cichu przy innym nazewnictwie w tenancie.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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

# Kanoniczne powody, których wolno użyć w prompcie/interpretacji.
CANONICAL_REASONS = tuple(_CANONICAL_TO_DISPLAY)


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


def resolve_time_off(
    intents: list[dict[str, Any]], reasons: TeamReasons
) -> list[dict[str, Any]]:
    """Intencje {weekday, powod} + powody zespołu → wpisy {weekday, reason_id, reason_name}.

    Pomija tylko wpisy, których w ogóle nie da się przypisać (pusty zespół powodów) — dzięki
    fallbackowi w ``TeamReasons.resolve`` w praktyce zachowuje wszystkie dni.
    """
    resolved: list[dict[str, Any]] = []
    for item in intents:
        try:
            weekday = int(item["weekday"])
        except (KeyError, ValueError, TypeError):
            continue
        if not 0 <= weekday <= 6:
            continue
        match = reasons.resolve(str(item.get("powod", "")))
        if match is None:
            continue
        reason_id, reason_name = match
        resolved.append(
            {"weekday": weekday, "reason_id": reason_id, "reason_name": reason_name}
        )
    return resolved
