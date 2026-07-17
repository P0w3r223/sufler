"""Testy serwisu zdarzeń (EventService, ADR 0019) — sanityzacja + dedup na atrapie w pamięci.

Rdzeń zależy tylko od portu ``EventStore``, więc pełną logikę testujemy atrapą bez SQLite.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from workmate.core.application.events import EventService
from workmate.core.domain.events import Event, NewEvent
from workmate.core.errors import WriteError

_WHEN = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)


class _FakeStore:
    """Atrapa ``EventStore`` w pamięci — dedup po kluczu, id rosnące, jak realny magazyn."""

    def __init__(self) -> None:
        self._rows: list[Event] = []

    def exists(self, source: str, external_id: str, kind: str) -> bool:
        return any(
            e.source == source and e.external_id == external_id and e.kind == kind
            for e in self._rows
        )

    def append(self, event: NewEvent) -> Event:
        existing = next(
            (
                e
                for e in self._rows
                if e.source == event.source
                and e.external_id == event.external_id
                and e.kind == event.kind
            ),
            None,
        )
        if existing is not None:
            return existing
        row = Event(id=len(self._rows) + 1, ingested_at=_WHEN, **event.model_dump())
        self._rows.append(row)
        return row

    def read_since(self, after_id, *, source=None, limit=50):
        hits = [e for e in self._rows if e.id > after_id and (source is None or e.source == source)]
        return hits[:limit]

    def recent(self, *, source=None, limit=20):
        hits = [e for e in reversed(self._rows) if source is None or e.source == source]
        return hits[:limit]


def _event(external_id: str = "1", *, kind: str = "issue_opened", **kw) -> NewEvent:
    base = {"source": "github", "kind": kind, "external_id": external_id, "occurred_at": _WHEN}
    base.update(kw)
    return NewEvent(**base)


def test_ingest_stores_new_event():
    service = EventService(_FakeStore())
    stored = service.ingest(_event("7", title="Nowe issue"))
    assert stored is not None
    assert stored.id == 1
    assert stored.external_id == "7"


def test_ingest_deduplicates_same_key():
    service = EventService(_FakeStore())
    first = service.ingest(_event("7"))
    second = service.ingest(_event("7"))
    assert first is not None
    assert second is None  # ten sam (source, external_id, kind) → pominięte


def test_ingest_same_id_different_kind_is_distinct():
    service = EventService(_FakeStore())
    assert service.ingest(_event("7", kind="issue_opened")) is not None
    assert service.ingest(_event("7", kind="issue_comment")) is not None  # inny kind = nowe


def test_ingest_rejects_control_characters():
    service = EventService(_FakeStore())
    with pytest.raises(WriteError):
        service.ingest(_event("7", title="zła\x00treść"))


def test_recent_returns_newest_first_and_filters_source():
    store = _FakeStore()
    service = EventService(store)
    service.ingest(_event("1"))
    store.append(NewEvent(source="teams", kind="note", external_id="n1", occurred_at=_WHEN))
    service.ingest(_event("2"))

    newest = service.recent(limit=10)
    assert [e.external_id for e in newest][:2] == ["2", "n1"]
    assert [e.external_id for e in service.recent(source="github")] == ["2", "1"]


def test_read_since_advances_by_id():
    service = EventService(_FakeStore())
    service.ingest(_event("1"))
    service.ingest(_event("2"))
    assert [e.external_id for e in service.read_since(1)] == ["2"]
    assert service.read_since(2) == []
