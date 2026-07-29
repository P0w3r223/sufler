"""Port metryk użycia (Tor A) — ``Protocol`` jak pozostałe porty rdzenia.

Wąski magazyn liczników: zapisz wywołanie (drzwi, pseudonim użytkownika, tydzień ISO) i zwróć
zwinięte zestawienie. Rdzeń zna TYLKO ten interfejs — pseudonimizacja i bucketing tygodnia stoją
w ``core/domain/metrics``, a fizyczny zapis w adapterze (SQLite). Reguła ``core ↛ adapters`` stoi.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from workmate.core.domain.metrics import MetricsSummary


class MetricsStore(Protocol):
    """Magazyn liczników wywołań: idempotentny UPSERT per (drzwi, użytkownik, tydzień) + odczyt."""

    def record_call(self, door: str, user_key: str, week: str, occurred_at: datetime) -> None:
        """Dolicz jedno wywołanie; kolejne w tym samym (drzwi, użytkownik, tydzień) inkrementują."""
        ...

    def summary(self) -> MetricsSummary:
        """Zwiń liczniki do zestawienia per drzwi (wywołania, unikalni, powracający)."""
        ...
