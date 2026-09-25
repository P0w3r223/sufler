"""Przypadek użycia wspólnego magazynu zdarzeń (EventStore, Faza 3 / ADR 0019).

Cienka orkiestracja nad portem ``EventStore`` (bez I/O — testowalna na atrapie w pamięci):
sanityzacja treści niezaufanej PRZED zapisem, deduplikacja, dopisanie i odczyt. Treść
zdarzenia pochodzi ze źródła NIEZAUFANEGO (GitHub) — traktujemy ją jak DANE, nie polecenia,
i odrzucamy znaki sterujące (obrona w głąb, jak ``NotesWriteService``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sufler.core.domain.sanitize import reject_dangerous_content

if TYPE_CHECKING:
    from sufler.core.domain.events import Event, NewEvent
    from sufler.core.ports.events import EventStore


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

    def echo_exists(self, external_id: str, kind: str) -> bool:
        """Czy w magazynie leży ECHO naszych drzwi zapisu o tym kluczu (ADR 0071 decyzja 6).

        Źródło ``teams`` nie jest parametrem z rozmysłu: echo to ślad, który zostawiają WYŁĄCZNIE
        nasze drzwi zapisu. Wołający podający inne źródło zadawałby inne pytanie — i dostałby na
        nie odpowiedź wyglądającą jak ta, co czyni z parametru pułapkę zamiast elastyczności.

        Dedup magazynu tego pytania NIE zamyka i nie wolno tego zakładać (ADR 0071 decyzja 7):
        klucz unikalności to ``(source, external_id, kind)``, więc echo ``('teams', '40',
        'github_issue_created')`` i zdarzenie pollera ``('github', '40', 'issue_opened')`` są dla
        bazy DWOMA różnymi faktami i oba przechodzą. Magazyn gwarantuje, że każdy zapisze się raz;
        nie ma pojęcia, że opisują to samo. Dlatego strażnik musi jawnie SPRAWDZIĆ klucz echa,
        a nie liczyć na ``ON CONFLICT``.
        """
        return self._store.exists("teams", external_id, kind)

    def recent(
        self, *, source: str | None = None, project: str | None = None, limit: int = 20
    ) -> list[Event]:
        """Ostatnie zdarzenia w kolejności PRZYJĘCIA (po ``id``), opcjonalnie zawężone.

        Na tej kolejności stoi bootstrap kursora MCP i jego zamrożony opis — konsument pytający
        o CZAS woła ``recent_by_time`` (amendment ADR 0071 z 2026-09-07).
        """
        return self._store.recent(source=source, project=project, limit=limit)

    def recent_by_time(
        self, *, source: str | None = None, project: str | None = None, limit: int = 20
    ) -> list[Event]:
        """Ostatnie zdarzenia w kolejności CZASU ZDARZENIA, opcjonalnie zawężone."""
        return self._store.recent_by_time(source=source, project=project, limit=limit)

    def read_since(
        self,
        after_id: int,
        *,
        source: str | None = None,
        project: str | None = None,
        limit: int = 50,
    ) -> list[Event]:
        """Zdarzenia nowsze niż kursor ``after_id`` (dla notifiera), rosnąco po id."""
        return self._store.read_since(after_id, source=source, project=project, limit=limit)
