"""Dziennik audytu na SQLite (implementacja ``AuditStore``, Faza 0 / ADR 0067).

OSOBNY plik i tabela od ``EventStore``/``conversations``/``metrics`` — dane operacyjne poza bazą
wiedzy, o innej retencji (ADR 0067: dłuższej niż rozmów, Faza 7). Ten sam wzorzec współbieżności co
pozostałe magazyny SQLite (``check_same_thread=False`` + ``Lock``, ``WAL`` + ``busy_timeout``) —
drzwi agentowe to OSOBNE procesy dzielące ten sam plik. Atomowość i trwałość jak w ADR 0045.

Wpis jest APPEND-ONLY: ``arg_summary`` już zredagowany w warstwie wyżej (``core.domain.audit``),
``actor_key``/``conversation_key`` to pseudonimy (``sha256[:16]``) — surowej tożsamości ani treści
baza nie widzi.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS audit_tool_calls (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        occurred_at      TEXT NOT NULL,
        actor_key        TEXT NOT NULL,
        conversation_key TEXT NOT NULL,
        door             TEXT NOT NULL,
        tool_name        TEXT NOT NULL,
        arg_summary      TEXT NOT NULL,
        status           TEXT NOT NULL,
        trust_class      TEXT NOT NULL,
        judge_verdict    TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_tool ON audit_tool_calls(tool_name);",
    "CREATE INDEX IF NOT EXISTS idx_audit_occurred ON audit_tool_calls(occurred_at);",
)

_COLUMNS = (
    "occurred_at",
    "actor_key",
    "conversation_key",
    "door",
    "tool_name",
    "arg_summary",
    "status",
    "trust_class",
    "judge_verdict",
)


class SqliteAuditStore:
    """``AuditStore`` na SQLite — append-only dziennik wywołań narzędzi."""

    def __init__(self, db_path: Path | str) -> None:
        if str(db_path) != ":memory:":
            Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.Lock()
        with self._lock:
            for stmt in _SCHEMA:
                self._conn.execute(stmt)
            self._conn.commit()

    def record_tool_call(
        self,
        *,
        occurred_at: datetime,
        actor_key: str,
        conversation_key: str,
        door: str,
        tool_name: str,
        arg_summary: str,
        status: str,
        trust_class: str,
        judge_verdict: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_tool_calls(occurred_at, actor_key, conversation_key, door, "
                "tool_name, arg_summary, status, trust_class, judge_verdict) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    occurred_at.isoformat(),
                    actor_key,
                    conversation_key,
                    door,
                    tool_name,
                    arg_summary,
                    status,
                    trust_class,
                    judge_verdict,
                ),
            )
            self._conn.commit()

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT " + ", ".join(_COLUMNS) + " FROM audit_tool_calls ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{col: row[col] for col in _COLUMNS} for row in rows]
