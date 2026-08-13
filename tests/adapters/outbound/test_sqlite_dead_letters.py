"""Testy magazynu kwarantanny SQLite (ADR 0067 §2): idempotencja + odczyt, najnowsze pierwsze."""

from __future__ import annotations

from datetime import datetime, timezone

from workmate.adapters.outbound.sqlite_dead_letters import SqliteDeadLetterStore


def _store() -> SqliteDeadLetterStore:
    return SqliteDeadLetterStore(
        ":memory:", clock=lambda: datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    )


def test_record_and_read_back():
    store = _store()
    store.record(source="github", event_id=42, reason="RuntimeError('Graph 503')", attempts=5)
    (row,) = store.recent()
    assert row["source"] == "github"
    assert row["event_id"] == 42
    assert row["attempts"] == 5
    assert "Graph 503" in row["reason"]
    assert row["failed_at"] == "2026-08-13T10:00:00+00:00"


def test_record_is_idempotent_on_source_event_id():
    store = _store()
    store.record(source="github", event_id=7, reason="pierwszy", attempts=5)
    store.record(source="github", event_id=7, reason="drugi", attempts=9)
    rows = store.recent()
    assert len(rows) == 1  # INSERT OR IGNORE — bez duplikatu
    assert rows[0]["reason"] == "pierwszy"  # pierwszy powód zachowany


def test_same_event_id_different_source_are_distinct():
    store = _store()
    store.record(source="github", event_id=1, reason="g", attempts=5)
    store.record(source="jira", event_id=1, reason="j", attempts=5)
    assert len(store.recent()) == 2


def test_recent_newest_first_and_limit():
    store = _store()
    for eid in (1, 2, 3):
        store.record(source="github", event_id=eid, reason="x", attempts=5)
    ids = [r["event_id"] for r in store.recent(limit=2)]
    assert ids == [3, 2]


def test_reason_is_capped():
    store = _store()
    store.record(source="github", event_id=1, reason="x" * 5000, attempts=5)
    assert len(store.recent()[0]["reason"]) == 1000


def test_recent_empty_store():
    assert _store().recent() == []
