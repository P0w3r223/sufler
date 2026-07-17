"""Przypadek użycia wspólnego magazynu zdarzeń (EventStore, Faza 3 / ADR 0019).

Cienka orkiestracja nad portem ``EventStore`` (bez I/O — testowalna na atrapie w pamięci):
sanityzacja treści niezaufanej PRZED zapisem, deduplikacja, dopisanie i odczyt. Treść
zdarzenia pochodzi ze źródła NIEZAUFANEGO (GitHub) — traktujemy ją jak DANE, nie polecenia,
i odrzucamy znaki sterujące (obrona w głąb, jak ``NotesWriteService``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from workmate.core.domain.sanitize import reject_dangerous_content

if TYPE_CHECKING:
    from workmate.core.domain.events import Event, NewEvent
    from workmate.core.ports.events import EventStore


class EventService:
    """Zgłaszanie i odczyt zdarzeń warstwy spajającej (sanityzacja + dedup nad portem)."""

    def __init__(self, store: EventStore) -> None:
        self._store = store

    def ingest(self, event: NewEvent) -> Event | None:
        """Zgłoś zdarzenie; zwróć zapisane albo ``None``, gdy już było (dedup).

        Najpierw sanityzacja pól tekstowych (treść źródła to DANE — odrzucamy znaki
        sterujące), potem szybki pre-check ``exists`` (żeby nie płacić zapisu na duplikacie).
        Sam ``append`` jest i tak idempotentny po kluczu — pre-check to optymalizacja i
        czytelny sygnał „to nowe" dla pollera.
        """
        reject_dangerous_content(event.title, event.summary, event.actor, event.url)
        if self._store.exists(event.source, event.external_id, event.kind):
            return None
        return self._store.append(event)

    def recent(self, *, source: str | None = None, limit: int = 20) -> list[Event]:
        """Ostatnie zdarzenia (najnowsze pierwsze), opcjonalnie zawężone do źródła."""
        return self._store.recent(source=source, limit=limit)

    def read_since(
        self, after_id: int, *, source: str | None = None, limit: int = 50
    ) -> list[Event]:
        """Zdarzenia nowsze niż kursor ``after_id`` (dla notifiera), rosnąco po id."""
        return self._store.read_since(after_id, source=source, limit=limit)
