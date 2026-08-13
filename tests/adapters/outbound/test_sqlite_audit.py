"""Testy magazynu audytu SQLite (ADR 0067): append + odczyt ostatnich, najnowsze pierwsze."""

from __future__ import annotations

from datetime import datetime, timezone

from workmate.adapters.outbound.sqlite_audit import SqliteAuditStore


def _at(minute: int) -> datetime:
    return datetime(2026, 8, 13, 10, minute, tzinfo=timezone.utc)


def _record(store: SqliteAuditStore, tool: str, minute: int, *, status: str = "ok") -> None:
    store.record_tool_call(
        occurred_at=_at(minute),
        actor_key="ab12cd34ef56ab78",
        conversation_key="99aa88bb77cc66dd",
        door="teams",
        tool_name=tool,
        arg_summary='{"action": "read"}',
        status=status,
        trust_class="unknown",
    )


def test_record_and_read_back():
    store = SqliteAuditStore(":memory:")
    _record(store, "File", 1)
    (row,) = store.recent()
    assert row["tool_name"] == "File"
    assert row["door"] == "teams"
    assert row["actor_key"] == "ab12cd34ef56ab78"
    assert row["arg_summary"] == '{"action": "read"}'
    assert row["status"] == "ok"
    assert row["trust_class"] == "unknown"
    assert row["judge_verdict"] is None


def test_recent_returns_newest_first_and_respects_limit():
    store = SqliteAuditStore(":memory:")
    _record(store, "Bash", 1)
    _record(store, "Notes", 2)
    _record(store, "GitHub", 3)
    tools = [r["tool_name"] for r in store.recent(limit=2)]
    assert tools == ["GitHub", "Notes"]  # najnowsze pierwsze, ucięte do 2


def test_recent_empty_store():
    assert SqliteAuditStore(":memory:").recent() == []


def test_status_and_judge_verdict_persisted():
    store = SqliteAuditStore(":memory:")
    store.record_tool_call(
        occurred_at=_at(5),
        actor_key="k",
        conversation_key="c",
        door="teams",
        tool_name="Notes",
        arg_summary="{}",
        status="error",
        trust_class="unknown",
        judge_verdict="refuse",
    )
    (row,) = store.recent()
    assert row["status"] == "error"
    assert row["judge_verdict"] == "refuse"
