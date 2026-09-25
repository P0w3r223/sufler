"""Testy magazynu audytu SQLite (ADR 0067): append + odczyt ostatnich, najnowsze pierwsze.

Większość sond biegnie na ``:memory:`` (szybko, bez śmieci). Sondy PLIKOWE są osobno i nie są
ozdobą: docstring adaptera obiecuje, że dziennik dzielą OSOBNE PROCESY drzwi przez jeden plik
(``check_same_thread=False`` + ``WAL``) — a tego ``:memory:`` nie potrafi sprawdzić z definicji,
bo każda baza w pamięci jest prywatna dla swojego połączenia.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sufler.adapters.outbound.sqlite_audit import SqliteAuditReader, SqliteAuditStore
from sufler.adapters.outbound.sqlite_readonly import MissingTableError


def _at(minute: int) -> datetime:
    return datetime(2026, 8, 13, 10, minute, tzinfo=UTC)


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


def test_recent_z_limitem_zero_nie_oddaje_nic():
    """``limit`` idzie wprost do SQL — sonda pilnuje, że zero znaczy zero, a nie „bez limitu"."""
    store = SqliteAuditStore(":memory:")
    _record(store, "Bash", 1)
    assert store.recent(limit=0) == []


def test_wpis_widzi_drugie_polaczenie_do_tego_samego_pliku(tmp_path: Path):
    """Drzwi agentowe to OSOBNE PROCESY nad jednym plikiem — zapis musi być widoczny „obok".

    Bez commitu po wstawce (albo z bazą trzymaną w pamięci procesu) czytelnik zobaczyłby pustkę,
    a dziennik audytu byłby kompletny wyłącznie z perspektywy procesu, który pisał.
    """
    db = tmp_path / "stan" / "audit.db"
    pisarz = SqliteAuditStore(db)
    _record(pisarz, "Notes", 1)

    czytelnik = SqliteAuditStore(db)  # drugie połączenie = symulacja drugich drzwi

    assert [r["tool_name"] for r in czytelnik.recent()] == ["Notes"]


def test_katalog_bazy_powstaje_gdy_go_nie_ma(tmp_path: Path):
    """Wolumen stanu bywa pusty przy pierwszym starcie — brak katalogu nie może wywalić drzwi."""
    db = tmp_path / "swiezy" / "poziom" / "audit.db"

    SqliteAuditStore(db)

    assert db.parent.is_dir()


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


# --- czytelnik dziennika (ADR 0069 R2) ----------------------------------------------------


def test_czytelnik_zwraca_najnowsze_pierwsze(tmp_path: Path):
    db = tmp_path / "audit.db"
    store = SqliteAuditStore(db)
    _record(store, "Project", 1)
    _record(store, "Activity", 5)

    assert [r["tool_name"] for r in SqliteAuditReader(db).entries()] == ["Activity", "Project"]


def test_czytelnik_filtruje_po_drzwiach_i_okresie(tmp_path: Path):
    db = tmp_path / "audit.db"
    store = SqliteAuditStore(db)
    _record(store, "Project", 1)
    store.record_tool_call(
        occurred_at=_at(30),
        actor_key="k",
        conversation_key="c",
        door="mcp",
        tool_name="save_note",
        arg_summary="{}",
        status="ok",
        trust_class="unknown",
    )
    czytelnik = SqliteAuditReader(db)

    assert [r["tool_name"] for r in czytelnik.entries(source="mcp")] == ["save_note"]
    assert [r["tool_name"] for r in czytelnik.entries(until=_at(10))] == ["Project"]


def test_czytelnik_dziennika_nie_pisze_i_nie_zaklada_tabeli(tmp_path: Path):
    """Dziennik jest append-only dla drzwi, a dla operatora — tylko do odczytu."""
    db = tmp_path / "audit.db"
    SqliteAuditStore(db)
    czytelnik = SqliteAuditReader(db)

    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        czytelnik._conn.execute("DELETE FROM audit_tool_calls")

    obca = tmp_path / "obca.db"
    sqlite3.connect(obca).close()
    with pytest.raises(MissingTableError, match="audit_tool_calls"):
        SqliteAuditReader(obca).entries()
