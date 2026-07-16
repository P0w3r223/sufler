"""Port wspólnego magazynu zdarzeń (EventStore, Faza 3 / ADR 0019).

``Protocol`` jak pozostałe porty — dowolna implementacja o zgodnych sygnaturach jest
akceptowana bez dziedziczenia. Domyślny adapter: SQLite (append-only, wieloprocesowy —
drzwi to osobne procesy dzielące jeden plik); w testach — atrapa w pamięci. Znaczniki
czasu i identyfikatory nadaje implementacja, nie rdzeń.

Magazyn jest APPEND-ONLY z deduplikacją po ``(source, external_id, kind)`` — to daje
polleremu semantykę „co najmniej raz" bez podwójnych wpisów przy wyścigu procesów.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from workmate.core.domain.events import Event, NewEvent


class EventStore(Protocol):
    """Trwałość zdarzeń: dedup, dopisywanie, kursor notifiera i podgląd ostatnich."""

    def exists(self, source: str, external_id: str, kind: str) -> bool:
        """Czy zdarzenie o kluczu ``(source, external_id, kind)`` już przyjęto (dedup pollingu)."""
        ...

    def append(self, event: NewEvent) -> Event:
        """Dopisz zdarzenie i zwróć je z nadanym id i znacznikiem przyjęcia.

        Idempotentne po kluczu ``(source, external_id, kind)``: przy kolizji NIE tworzy dubla,
        tylko zwraca wpis już istniejący — bezpieczne przy równoległych procesach (at-least-once).
        """
        ...

    def read_since(
        self, after_id: int, *, source: str | None = None, limit: int = 50
    ) -> list[Event]:
        """Zdarzenia o ``id`` > ``after_id`` (kursor notifiera), rosnąco po id."""
        ...

    def recent(self, *, source: str | None = None, limit: int = 20) -> list[Event]:
        """Ostatnie zdarzenia (najnowsze pierwsze) do podglądu przez narzędzie agenta."""
        ...
