"""Port kwarantanny zdarzeń niewysyłalnych (Faza 0, ADR 0067 §2) — ``Protocol`` jak inne porty.

Gdy zdarzenia nie da się wysłać po N próbach, notifier przenosi je TU (z powodem), i DOPIERO POTEM
przesuwa kursor — zdarzenie trwale niewysyłalne (poison message) nie blokuje strumienia, a
inwariant „co najmniej raz" z ADR 0022 zostaje: wpis jest trwały, zanim kursor je minie.

Idempotentny po ``(source, event_id)`` — powtórny zapis tego samego zdarzenia (np. po restarcie)
nie duplikuje. Rdzeń zna TYLKO ten interfejs; fizyczny zapis w adapterze (SQLite nad ``events.db``).
"""

from __future__ import annotations

from typing import Any, Protocol


class DeadLetterStore(Protocol):
    """Magazyn zdarzeń trwale niewysyłalnych: przenieś (idempotentnie) + odczyt ostatnich."""

    def record(self, *, source: str, event_id: int, reason: str, attempts: int) -> None:
        """Zapisz zdarzenie do kwarantanny. Idempotentny po ``(source, event_id)``."""
        ...

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        """Ostatnie wpisy kwarantanny (najnowsze pierwsze) — do diagnozy i testów."""
        ...
