"""Parsowanie znaczników ISO 8601 na świadome (aware) ``datetime`` — wspólne dla obu źródeł.

Claude Code zapisuje UTC z sufiksem ``Z`` (którego ``datetime.fromisoformat`` nie zna do
Pythona 3.11), a ``git`` (``%aI``) podaje offset. Jedno miejsce normalizacji ``Z`` → ``+00:00``
i uzupełnienia brakującej strefy na UTC gwarantuje, że grupowanie po dniu ma jednoznaczny
punkt odniesienia niezależnie od źródła.
"""

from __future__ import annotations

from datetime import datetime, timezone


def parse_iso(value: str) -> datetime:
    """Sparsuj znacznik ISO 8601 na ``datetime`` ze strefą (naiwny → UTC).

    Rzuca ``ValueError``, gdy tekst nie jest poprawnym ISO 8601 — wołający degraduje
    (pomija wpis), zamiast wywracać cały bieg z powodu jednej uszkodzonej linii.
    """
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
