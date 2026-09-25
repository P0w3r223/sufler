"""Wspólne formatery renderu jednostronicówek (F4/F5) — jedno źródło (ADR 0051/0052).

Drobne, czyste helpery dat/znaczników czasu współdzielone przez ``ProjectBrief.to_text``
(one-pager) i ``ChangeDigest.to_text`` (co się zmieniło) — żeby oba rendery formatowały tak
samo, bez duplikacji. Bez zależności od locale: daty ISO, brak → kreska.
"""

from __future__ import annotations

from datetime import date, datetime

_MISSING = "—"


def fmt_date(value: date | None) -> str:
    """Data ISO (``YYYY-MM-DD``) albo kreska, gdy brak (``None``)."""
    return value.isoformat() if value is not None else _MISSING


def fmt_seconds(value: datetime | None) -> str:
    """Znacznik czasu do sekund (bez strefy/mikrosekund) albo kreska, gdy brak."""
    if value is None:
        return _MISSING
    text = value.isoformat()
    return text[:19] if len(text) > 19 else text


def fmt_counts(counts: tuple[tuple[str, int], ...]) -> str:
    """Sklej pary ``(etykieta, liczba)`` w ``a: 3 · b: 1`` albo kreskę, gdy pusto."""
    return " · ".join(f"{label}: {count}" for label, count in counts) or _MISSING
