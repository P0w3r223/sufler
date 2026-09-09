"""Złożenie digestu „co się zmieniło od <data>" (ADR 0052, F5) — read-only, deterministyczne.

Fold zdarzeń warstwy spajającej: ``EventService.recent_by_time`` (najnowsze wg CZASU ZDARZENIA)
filtrowany po ``occurred_at.date() >= since``, pogrupowany po projekcie i źródle. Magazyn nie ma
zapytania po dacie, więc skanujemy ``scan_limit`` najnowszych i filtrujemy w pamięci; przy
trafieniu w sufit skanu z trafieniami w oknie ustawiamy ``truncated`` (okno mogło mieć więcej —
nie ucinamy po cichu). Brak mostu → pusto.

Do 2026-09-07 skan szedł po ``id`` (kolejności PRZYJĘCIA), a ten docstring mówił: „duży backfill
starych zdarzeń osłabiłby to założenie, ale pipeline go nie robi". Decyzja 9 ADR 0071 **jest tym
backfillem**, więc założenie przestało obowiązywać i skan przeszedł na czas (amendment ADR 0071).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import date
from typing import TYPE_CHECKING

from workmate.core.domain.change_digest import ChangeDigest, ProjectChanges

if TYPE_CHECKING:
    from workmate.core.application.events import EventService
    from workmate.core.domain.events import Event

# Sufit skanowanego okna najnowszych zdarzeń — digest STRESZCZA zmiany, nie odtwarza strumienia.
DEFAULT_SCAN_LIMIT = 500


class ChangeDigestService:
    """Buduje ``ChangeDigest`` od zadanej daty z zdarzeń warstwy spajającej (odczyt)."""

    def __init__(
        self, events: EventService | None, *, scan_limit: int = DEFAULT_SCAN_LIMIT
    ) -> None:
        # ``None`` → drzwi bez mostu zdarzeń; digest jest wtedy pusty (dozwolona degradacja).
        self._events = events
        self._scan_limit = scan_limit

    def since(self, day: date) -> ChangeDigest:
        """Złóż digest zmian od ``day`` (włącznie). Bez mostu zdarzeń → pusty digest."""
        if self._events is None:
            return ChangeDigest(since=day, total=0, by_source=(), projects=(), truncated=False)
        scanned = self._events.recent_by_time(limit=self._scan_limit)  # najnowsze wg CZASU
        window = [e for e in scanned if e.occurred_at.date() >= day]
        # Ucięcie NIEZALEŻNE od kolejności: trafienie w sufit skanu Z trafieniami w oknie znaczy, że
        # poza sufitem mogą być kolejne zdarzenia z okna.
        #
        # Docstring tej klasy mówił do 2026-09-07: „duży backfill starych zdarzeń osłabiłby to
        # założenie, ale pipeline go nie robi". Decyzja 9 ADR 0071 JEST tym backfillem — dlatego
        # skan idzie teraz po ``occurred_at``, a nie po ``id``. Przy sorcie po czasie ucięcie
        # przestaje być przybliżeniem dla starej ``since``: okno jest prefiksem skanu, więc flaga
        # mówi dokładnie to, co znaczy. Zostaje liczona z DŁUGOŚCI, nie z pozycji elementu —
        # równe ``occurred_at`` nie dają gwarancji, który wiersz baza zwróci pierwszy.
        truncated = len(scanned) >= self._scan_limit and bool(window)
        return ChangeDigest(
            since=day,
            total=len(window),
            by_source=_counts(e.source for e in window),
            projects=_group_by_project(window),
            truncated=truncated,
        )


def _group_by_project(events: list[Event]) -> tuple[ProjectChanges, ...]:
    """Pogrupuj zdarzenia po projekcie; sekcje malejąco po liczbie, potem po kluczu (stabilnie)."""
    by_project: dict[str, list[Event]] = {}
    for event in events:
        by_project.setdefault(event.project, []).append(event)
    changes = [
        ProjectChanges(
            project=project,
            total=len(items),
            by_kind=_counts(e.kind for e in items),
            latest=max((e.occurred_at for e in items), default=None),
        )
        for project, items in by_project.items()
    ]
    changes.sort(key=lambda c: (-c.total, c.project))
    return tuple(changes)


def _counts(labels: Iterable[str]) -> tuple[tuple[str, int], ...]:
    """Policz etykiety → pary ``(etykieta, liczba)`` malejąco po liczbie, remis alfabetycznie."""
    counter = Counter(labels)
    return tuple(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))
