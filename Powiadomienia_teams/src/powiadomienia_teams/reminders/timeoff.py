"""Powody nieobecności (język pracownika) → powód Shifts (timeOffReason).

Czysta logika. Interpreter zwraca kanoniczny `powod` z wąskiej listy; tutaj tłumaczymy go na
nazwę wyświetlaną w Shifts, a `TeamReasons` (zbudowane z żywych powodów zespołu) rozstrzyga go na
konkretne id (``TOR_…``) i FAKTYCZNĄ nazwę wyświetlaną — z fallbackiem do istniejącego powodu,
żeby dzień wolny nigdy nie zginął po cichu przy innym nazewnictwie w tenancie.
"""
from __future__ import annotations

from typing import Any

# Słownictwo powodów mieszka w ``domain.powody``: potrzebuje go także ``graph.client``,
# a import przez ten moduł domykał cykl graph → reminders → graph. Re-eksport zostaje,
# bo `TeamReasons` jest tu częścią kontraktu `resolve_time_off`.
from powiadomienia_teams.domain.powody import TeamReasons

__all__ = ["TeamReasons", "resolve_time_off"]


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
