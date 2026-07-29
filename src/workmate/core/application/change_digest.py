"""Złożenie digestu „co się zmieniło od <data>" (ADR 0052, F5) — read-only, deterministyczne.

Fold zdarzeń warstwy spajającej: ``EventService.recent`` (najnowsze pierwsze) filtrowany po
``occurred_at.date() >= since``, pogrupowany po projekcie i źródle. Magazyn nie ma zapytania po
dacie, więc skanujemy okno ``scan_limit`` najnowszych i filtrujemy w pamięci — deterministycznie,
bez zmiany portu ``EventStore``. Gdy nawet NAJSTARSZE zeskanowane zdarzenie mieści się w oknie,
mogło ich być więcej (``truncated``) — nie ucinamy po cichu. Brak mostu zdarzeń → pusty digest.
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
        scanned = self._events.recent(limit=self._scan_limit)  # najnowsze pierwsze
        window = [e for e in scanned if e.occurred_at.date() >= day]
        # Ucięcie: trafiliśmy w sufit, a najstarsze zeskanowane wciąż mieści się w oknie → poza
        # sufitem mogą być kolejne zdarzenia z okna, których nie policzyliśmy.
        truncated = len(scanned) >= self._scan_limit and bool(window) and window[-1] is scanned[-1]
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
