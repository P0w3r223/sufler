"""Kwarantanna zdarzeń niewysyłalnych na SQLite (implementacja ``DeadLetterStore``, ADR 0067 §2).

Tabela SIOSTRA ``events`` w tym samym pliku ``events.db`` (wolumen ``state``) — to metadane dostawy,
nie baza wiedzy. Ten sam wzorzec współbieżności co pozostałe magazyny SQLite
(``check_same_thread=False`` + ``Lock``, ``WAL`` + ``busy_timeout``); WAL dopuszcza drugie
połączenie do tego samego pliku obok ``SqliteEventStore``.

Wpis jest idempotentny po ``(source, event_id)`` (``INSERT OR IGNORE`` + ``UNIQUE``): powtórne
przeniesienie tego samego zdarzenia (np. po restarcie, gdy licznik prób w pamięci ruszył od zera)
nie duplikuje ani nie nadpisuje pierwszego powodu/czasu. ``failed_at`` to chwila KWARANTANNY (N-tej
porażki), nie pierwszej — licznik prób żyje w pamięci notifiera i nie zna czasu pierwszej porażki.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Powód (tekst wyjątku transportu) przycinamy — to komunikat błędu, nie treść zdarzenia, ale
# nie chcemy, żeby wyjątek niosący duży ładunek rozdął tabelę.
_MAX_REASON_CHARS = 1000

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS dead_letters (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        source      TEXT NOT NULL,
        event_id    INTEGER NOT NULL,
        reason      TEXT NOT NULL,
        attempts    INTEGER NOT NULL,
        failed_at   TEXT NOT NULL,
        UNIQUE(source, event_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_dead_letters_source ON dead_letters(source);",
)

_COLUMNS = ("source", "event_id", "reason", "attempts", "failed_at")


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class SqliteDeadLetterStore:
    """``DeadLetterStore`` na SQLite — kwarantanna zdarzeń niewysyłalnych, idempotentna."""

    def __init__(self, db_path: Path | str, *, clock: Callable[[], datetime] = _utcnow) -> None:
        if str(db_path) != ":memory:":
            Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._clock = clock
        self._lock = threading.Lock()
        with self._lock:
            for stmt in _SCHEMA:
                self._conn.execute(stmt)
            self._conn.commit()

    def record(self, *, source: str, event_id: int, reason: str, attempts: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO dead_letters(source, event_id, reason, attempts, failed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (source, event_id, reason[:_MAX_REASON_CHARS], attempts, self._clock().isoformat()),
            )
            self._conn.commit()

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT " + ", ".join(_COLUMNS) + " FROM dead_letters ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{col: row[col] for col in _COLUMNS} for row in rows]
