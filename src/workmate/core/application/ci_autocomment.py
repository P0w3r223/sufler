"""Deterministyczny auto-komentarz CI (ADR 0024, Faza 2) — autonomiczna, ale BEZPIECZNA ścieżka.

Konsument wspólnego magazynu zdarzeń: czyta zdarzenia ``ci_failure`` (``source="github"``) od
własnego kursora i — gdy porażka dotyczy PR — dopisuje na tym PR DETERMINISTYCZNY komentarz
(nie-LLM, ``core.domain.ci``). To jedyny autonomiczny zapis mostu; cała treść generowana modelem
zostaje bramkowana potwierdzeniem (ADR 0006). Zależy WYŁĄCZNIE od serwisów rdzenia (``EventService``
+ ``GithubWriteService``) — bez I/O i SDK, testowalny na atrapach; ``core ↛ adapters`` zachowane.

Niezmienniki:
- **Idempotencja (jeden run×attempt = jeden komentarz):** przed komentarzem „zaklepujemy" znacznik
  ``NewEvent(source="teams", kind="ci_autocomment", external_id=run_id#attempt)``; dedup magazynu
  po ``(source, external_id, kind)`` sprawia, że drugie przejście tego samego zdarzenia to no-op.
- **Kolejność claim → comment (at-most-once):** przy awarii między krokami raczej GUBIMY komentarz
  niż spamujemy PR — porażka CI i tak trafia do Teams przez notifiera, więc utrata jest OK.
- **Strażniki pętli:** znacznik i komentarz mają odzew ``source="teams"``, którego notifier (wypycha
  tylko ``source="github"``) nie odsyła; a re-ingest komentarza przez poller odpada na self-skip
  (autor = konto PAT). Zapis idzie przez bramkowany ``GithubWriteService`` (Gate 4).
- **Jeden pisarz znaczników:** pre-check ``exists`` w ``ingest`` ma okno wyścigu, więc zakładamy
  pojedynczy proces drzwi GitHub jako jedynego autora znaczników ``ci_autocomment`` (tak jest dziś).
- **Trwałość kursora robi ADAPTER, nie serwis:** ``process_once`` przesuwa kursor tylko W PAMIĘCI
  (``cursor``), a zapis do pliku stanu wykonuje adapter na wątku pętli PO powrocie z puli wątków —
  inaczej pisalibyśmy współbieżnie ten sam plik stanu co poller/notifier (uszkodzenie/wyścig).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from workmate.core.domain.ci import pr_number_from_url, render_ci_failure_comment
from workmate.core.domain.events import NewEvent

if TYPE_CHECKING:
    from workmate.core.application.events import EventService
    from workmate.core.application.github import GithubWriteService
    from workmate.core.domain.events import Event

logger = logging.getLogger(__name__)

_TRIGGER_KIND = "ci_failure"
# Znacznik idempotencji: ``source="teams"`` (poza pętlą notifiera), osobny ``kind`` do dedupu.
_MARKER_SOURCE = "teams"
_MARKER_KIND = "ci_autocomment"


class CiAutoCommentService:
    """Dopisuje deterministyczny komentarz na PR przy porażce CI (autonomicznie, idempotentnie)."""

    def __init__(
        self,
        events: EventService,
        writer: GithubWriteService,
        *,
        cursor: int = 0,
        source: str = "github",
    ) -> None:
        self._events = events
        self._writer = writer
        self._cursor = cursor
        self._source = source

    @property
    def cursor(self) -> int:
        """Pozycja kursora (id ostatnio przetworzonego zdarzenia).

        Trwałość DELEGUJE adapter (wątek pętli, po rundzie), więc serwis pozostaje bez I/O.
        """
        return self._cursor

    def process_once(self) -> int:
        """Obsłuż zdarzenia nowsze niż kursor; zwróć liczbę dopisanych komentarzy.

        Kursor przesuwamy W PAMIĘCI po każdym zdarzeniu niezależnie od wyniku (at-most-once): przed
        dublem chroni znacznik, a nie kursor. Trwałość robi adapter (wątek pętli). Błąd jednego
        zdarzenia nie kładzie rundy (best-effort). Reprocessing po restarcie jest bezpieczny — dedup
        znacznika i tak nie dopuści drugiego komentarza dla tego samego run×attempt.
        """
        batch = self._events.read_since(self._cursor, source=self._source, limit=50)
        posted = 0
        for event in batch:
            try:
                if self._handle(event):
                    posted += 1
            except Exception:
                logger.exception(
                    "Auto-komentarz CI pominięty dla zdarzenia %s (%s)",
                    event.external_id,
                    event.kind,
                )
            self._cursor = event.id
        return posted

    def _handle(self, event: Event) -> bool:
        """Zaklep znacznik i (jeśli nowy) dopisz komentarz na PR. Zwróć czy komentarz powstał."""
        if event.kind != _TRIGGER_KIND:
            return False
        pr_number = pr_number_from_url(event.url)
        if pr_number is None:
            return False  # porażka CI niezwiązana z PR — nie ma czego komentować
        marker = self._events.ingest(
            NewEvent(
                source=_MARKER_SOURCE,
                kind=_MARKER_KIND,
                external_id=event.external_id,
                title=event.title,
                url=event.url,
                occurred_at=event.occurred_at,
            )
        )
        if marker is None:
            return False  # znacznik już istnieje — ten run×attempt obsłużono, idempotentny no-op
        body = render_ci_failure_comment(event.title, event.summary)
        self._writer.create_comment(pr_number, body)
        return True
