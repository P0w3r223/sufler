"""Testy adaptera SQLite magazynu zdarzeń (SqliteEventStore, ADR 0019).

Realny plik tymczasowy: append/exists/read_since/recent, deduplikacja przez UNIQUE +
ON CONFLICT oraz współbieżność wieloprocesowa (dwa niezależne połączenia do tego samego pliku).
"""
from __future__ import annotations

from datetime import datetime, timezone

from workmate.adapters.outbound.sqlite_events import SqliteEventStore
from workmate.core.domain.events import NewEvent

_WHEN = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)


def _event(external_id: str, *, kind: str = "issue_opened", **kw) -> NewEvent:
    base = {
        "source": "github",
        "kind": kind,
        "external_id": external_id,
        "occurred_at": _WHEN,
    }
    base.update(kw)
    return NewEvent(**base)


def test_append_assigns_id_and_ingested_at(tmp_path):
    store = SqliteEventStore(tmp_path / "events.db")
    ev = store.append(_event("7", title="Nowe issue", actor="alice", url="http://x/7"))
    assert ev.id == 1
    assert ev.external_id == "7"
    assert ev.title == "Nowe issue"
    assert ev.actor == "alice"
    assert ev.ingested_at is not None
    assert ev.occurred_at == _WHEN


def test_exists_reflects_key(tmp_path):
    store = SqliteEventStore(tmp_path / "events.db")
    assert store.exists("github", "7", "issue_opened") is False
    store.append(_event("7"))
    assert store.exists("github", "7", "issue_opened") is True
    assert store.exists("github", "7", "issue_comment") is False  # inny kind


def test_append_dedup_on_conflict_keeps_first(tmp_path):
    store = SqliteEventStore(tmp_path / "events.db")
    first = store.append(_event("7", title="oryginał"))
    again = store.append(_event("7", title="zmieniony"))
    assert again.id == first.id  # brak dubla
    assert again.title == "oryginał"  # ON CONFLICT DO NOTHING — pierwotny wpis zostaje
    assert len(store.recent(limit=50)) == 1


def test_read_since_and_recent_ordering(tmp_path):
    store = SqliteEventStore(tmp_path / "events.db")
    store.append(_event("1"))
    store.append(_event("2"))
    store.append(_event("3"))

    since = store.read_since(1)
    assert [e.external_id for e in since] == ["2", "3"]  # rosnąco po id, kursor notifiera
    recent = store.recent(limit=2)
    assert [e.external_id for e in recent] == ["3", "2"]  # najnowsze pierwsze


def test_source_filter(tmp_path):
    store = SqliteEventStore(tmp_path / "events.db")
    store.append(_event("1"))
    store.append(NewEvent(source="teams", kind="note", external_id="n1", occurred_at=_WHEN))
    store.append(_event("2"))
    assert [e.external_id for e in store.recent(source="github")] == ["2", "1"]
    assert [e.external_id for e in store.read_since(0, source="teams")] == ["n1"]


def test_two_connections_share_file(tmp_path):
    """Wieloproces: drzwi GitHub piszą, notifier czyta — dwa połączenia, jeden plik (WAL)."""
    path = tmp_path / "events.db"
    writer = SqliteEventStore(path)
    reader = SqliteEventStore(path)
    writer.append(_event("7", title="z drzwi github"))
    hits = reader.read_since(0)
    assert [e.external_id for e in hits] == ["7"]
    assert reader.exists("github", "7", "issue_opened") is True
