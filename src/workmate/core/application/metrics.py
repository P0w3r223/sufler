"""Przypadek użycia metryk (Tor A): zarejestruj wywołanie i zwróć zestawienie.

Cienka orkiestracja nad portem ``MetricsStore`` + czyste funkcje domeny (pseudonim + tydzień ISO).
Zależy WYŁĄCZNIE od portu — testowalna na atrapie w pamięci, bez bazy. Rejestracja jest
best-effort z perspektywy drzwi: to metryka poboczna, nie może wywrócić tury (opakowanie w
try/except należy do szwu drzwi, nie tutaj — tu logika jest czysta).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from workmate.core.domain.metrics import iso_week, pseudonymize

if TYPE_CHECKING:
    from datetime import datetime

    from workmate.core.domain.metrics import MetricsSummary
    from workmate.core.ports.metrics import MetricsStore


class MetricsService:
    """Rejestruje wywołania per drzwi (z pseudonimem nadawcy) i zwija je do zestawienia."""

    def __init__(self, store: MetricsStore) -> None:
        self._store = store

    def record(self, door: str, raw_user: str, occurred_at: datetime) -> None:
        """Zapisz jedno wywołanie: pseudonimizuj nadawcę i zbucketuj po tygodniu ISO."""
        self._store.record_call(
            door, pseudonymize(raw_user), iso_week(occurred_at), occurred_at
        )

    def summary(self) -> MetricsSummary:
        """Zestawienie per drzwi: wywołania, unikalni użytkownicy, powracający (≥2 tygodnie)."""
        return self._store.summary()
