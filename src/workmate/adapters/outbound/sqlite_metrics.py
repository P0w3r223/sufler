"""Licznik wywołań na SQLite (implementacja ``MetricsStore``, Tor A).

OSOBNY plik i tabela od ``EventStore``/``conversations`` — metryki to dane operacyjne poza bazą
wiedzy (``data/``), nadpisywalne, o niskiej wadze. Ten sam wzorzec współbieżności co pozostałe
magazyny SQLite (połączenie ``check_same_thread=False`` + ``Lock``, ``WAL`` + ``busy_timeout``) —
drzwi agentowe (teams/telegram/cli) to OSOBNE procesy dzielące ten sam plik.

Ziarno = ``(door, user_key, week)`` z UPSERT-em inkrementującym ``call_count``. Dzięki temu:
- „wywołania/drzwi" = ``SUM(call_count)``,
- „unikalni użytkownicy" = ``COUNT(DISTINCT user_key)``,
- „powracający" = użytkownicy obecni w ≥2 różnych tygodniach (``COUNT(DISTINCT week) >= 2``).
``user_key`` to już pseudonim (hash z rdzenia) — surowej tożsamości baza nie widzi.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from workmate.core.domain.metrics import DoorUsage, MetricsSummary

if TYPE_CHECKING:
    from datetime import datetime

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS metrics_calls (
        door       TEXT NOT NULL,
        user_key   TEXT NOT NULL,
        week       TEXT NOT NULL,
        call_count INTEGER NOT NULL DEFAULT 0,
        -- first_seen/last_seen: rezerwa pod przyszły raport „ostatnia aktywność"; dziś summary ich
        -- nie czyta (liczy tylko wywołania/unikalnych/powracających), ale utrzymanie jest darmowe.
        first_seen TEXT NOT NULL,
        last_seen  TEXT NOT NULL,
        PRIMARY KEY (door, user_key, week)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_metrics_door ON metrics_calls(door);",
)


class SqliteMetricsStore:
    """``MetricsStore`` na SQLite — licznik wywołań per (drzwi, pseudonim, tydzień)."""

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

    def record_call(self, door: str, user_key: str, week: str, occurred_at: datetime) -> None:
        stamp = occurred_at.isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO metrics_calls(door, user_key, week, call_count, first_seen, "
                "last_seen) VALUES (?, ?, ?, 1, ?, ?) "
                "ON CONFLICT(door, user_key, week) DO UPDATE SET "
                "call_count = call_count + 1, last_seen = excluded.last_seen",
                (door, user_key, week, stamp, stamp),
            )
            self._conn.commit()

    def summary(self) -> MetricsSummary:
        with self._lock:
            calls = {
                str(r["door"]): int(r["c"])
                for r in self._conn.execute(
                    "SELECT door, SUM(call_count) AS c FROM metrics_calls GROUP BY door"
                )
            }
            unique = {
                str(r["door"]): int(r["c"])
                for r in self._conn.execute(
                    "SELECT door, COUNT(DISTINCT user_key) AS c FROM metrics_calls GROUP BY door"
                )
            }
            returning = {
                str(r["door"]): int(r["c"])
                for r in self._conn.execute(
                    "SELECT door, COUNT(*) AS c FROM ("
                    "  SELECT door, user_key FROM metrics_calls "
                    "  GROUP BY door, user_key HAVING COUNT(DISTINCT week) >= 2"
                    ") GROUP BY door"
                )
            }
        doors = sorted(calls, key=lambda d: calls[d], reverse=True)
        return MetricsSummary(
            by_door=tuple(
                DoorUsage(
                    door=d,
                    calls=calls[d],
                    unique_users=unique.get(d, 0),
                    returning_users=returning.get(d, 0),
                )
                for d in doors
            )
        )
