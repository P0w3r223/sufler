"""Testy foldu digestu zmian (``ChangeDigestService.since``, F5, ADR 0052).

Bez sieci: atrapa ``EventService`` (metoda ``recent``) zwraca ustaloną listę zdarzeń NAJNOWSZE
PIERWSZE. Kluczowe niezmienniki: filtr ``occurred_at.date() >= since``; grupowanie po projekcie i
malejące sortowanie; liczniki wg źródła; ``truncated`` gdy sufit skanu obcina okno; brak mostu →
pusty digest.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from workmate.core.application.change_digest import ChangeDigestService
from workmate.core.domain.events import Event


def _event(
    *,
    day: int,
    source: str = "github",
    kind: str = "pr_merged",
    project: str = "workmate",
    ident: int = 1,
) -> Event:
    ts = datetime(2026, 7, day, 12, 0, 0, tzinfo=UTC)
    return Event(
        id=ident,
        source=source,
        kind=kind,
        external_id=str(ident),
        project=project,
        occurred_at=ts,
        ingested_at=ts,
    )


class _FakeEvents:
    """Atrapa ``EventService``: ``recent`` zwraca ustaloną listę (najnowsze pierwsze), przyciętą."""

    def __init__(self, events: list[Event]) -> None:
        self._events = events

    def recent(self, *, source=None, project=None, limit: int = 20) -> list[Event]:  # noqa: ANN001
        return list(self._events[:limit])


def test_since_filters_events_before_the_date():
    events = [_event(day=20, ident=3), _event(day=10, ident=2), _event(day=1, ident=1)]

    digest = ChangeDigestService(_FakeEvents(events)).since(date(2026, 7, 5))

    # Tylko zdarzenia z dnia >= 5 lipca (20 i 10); 1 lipca odpada.
    assert digest.total == 2


def test_since_groups_by_project_sorted_by_volume():
    events = [
        _event(day=20, project="workmate", ident=4),
        _event(day=19, project="workmate", ident=3),
        _event(day=18, project="scada-integration", ident=2),
    ]

    digest = ChangeDigestService(_FakeEvents(events)).since(date(2026, 7, 1))

    keys = [p.project for p in digest.projects]
    assert keys == ["workmate", "scada-integration"]  # więcej zdarzeń → wyżej
    assert digest.projects[0].total == 2


def test_since_counts_by_source():
    events = [
        _event(day=20, source="github", ident=3),
        _event(day=19, source="github", ident=2),
        _event(day=18, source="jira", ident=1),
    ]

    digest = ChangeDigestService(_FakeEvents(events)).since(date(2026, 7, 1))

    assert digest.by_source == (("github", 2), ("jira", 1))


def test_since_counts_in_window_regardless_of_scan_order():
    # ``recent`` sortuje po id (ingestii), NIE po occurred_at — filtr/fold nie mogą zakładać
    # kolejności po dacie. Mieszamy porządek: wynik zależy od zawartości okna, nie pozycji.
    events = [_event(day=10, ident=5), _event(day=20, ident=4), _event(day=7, ident=3)]

    digest = ChangeDigestService(_FakeEvents(events)).since(date(2026, 7, 8))

    assert digest.total == 2  # day10 i day20 (>= 8); day7 odpada — bez względu na kolejność skanu


def test_since_marks_truncated_when_scan_cap_hits_within_window():
    # Sufit 2, a wszystkie 3 zdarzenia są w oknie: skanujemy tylko 2 najnowsze, więc poza sufitem
    # mogą być kolejne → truncated. Liczymy tylko to, co zeskanowaliśmy.
    events = [_event(day=20, ident=3), _event(day=15, ident=2), _event(day=10, ident=1)]

    digest = ChangeDigestService(_FakeEvents(events), scan_limit=2).since(date(2026, 7, 1))

    assert digest.truncated is True
    assert digest.total == 2


def test_since_not_truncated_when_boundary_reached():
    # Sufit (5) obejmuje zdarzenie sprzed okna (1 lipca) → najstarsze zeskanowane jest PRZED oknem,
    # więc granicę widzieliśmy i okno jest pełne (mimo trafienia w treść).
    events = [_event(day=20, ident=3), _event(day=10, ident=2), _event(day=1, ident=1)]

    digest = ChangeDigestService(_FakeEvents(events), scan_limit=5).since(date(2026, 7, 5))

    assert digest.truncated is False


def test_since_without_event_bridge_is_empty():
    digest = ChangeDigestService(None).since(date(2026, 7, 1))

    assert digest.total == 0
    assert digest.projects == ()
    assert digest.by_source == ()
    assert digest.truncated is False


def test_since_no_events_in_window_is_empty():
    events = [_event(day=1, ident=1)]

    digest = ChangeDigestService(_FakeEvents(events)).since(date(2026, 7, 10))

    assert digest.total == 0
    assert digest.projects == ()
