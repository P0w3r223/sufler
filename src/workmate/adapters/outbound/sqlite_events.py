"""Wspólny magazyn zdarzeń oparty o SQLite (implementacja ``EventStore``, ADR 0019).

Warstwa SPAJAJĄCA drzwi: drzwi GitHub piszą zdarzenia, notifier je czyta i wypycha do Teams,
narzędzie agenta czyta ostatnie — wszystko z jednego pliku. Dlaczego OSOBNY plik od
``conversations.db``: tamten robi rebuild tabeli przy migracji FK (DROP/RENAME pod
``foreign_keys=OFF``), a obca tabela komplikowałaby to i blokowała zapisy w trakcie; osobny
plik ma niezależny cykl życia i schemat. Ten sam wzorzec współbieżności co rozmowy: jedno
połączenie ``check_same_thread=False`` + ``Lock``, ``WAL`` + ``busy_timeout`` (drzwi to
osobne PROCESY dzielące plik). Znaczniki czasu przyjęcia nadaje baza (``CURRENT_TIMESTAMP``),
id — ``AUTOINCREMENT``: rdzeń nie woła zegara.

APPEND-ONLY z deduplikacją: ``UNIQUE(source, external_id, kind)`` + ``INSERT … ON CONFLICT DO
NOTHING`` — równoległe procesy nie utworzą dubla, a poller ma bezpieczne „co najmniej raz".
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workmate.core.domain.events import Event, NewEvent

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        source      TEXT NOT NULL,
        kind        TEXT NOT NULL,
        external_id TEXT NOT NULL,
        actor       TEXT NOT NULL DEFAULT '',
        title       TEXT NOT NULL DEFAULT '',
        summary     TEXT NOT NULL DEFAULT '',
        url         TEXT NOT NULL DEFAULT '',
        occurred_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
        UNIQUE(source, external_id, kind)
    );
    """,
    # Kursor notifiera i podgląd po źródle idą po (source, id) — jeden indeks pokrywa oba.
    "CREATE INDEX IF NOT EXISTS idx_events_source_id ON events(source, id);",
)


class SqliteEventStore:
    """``EventStore`` na SQLite — append-only, wieloprocesowy (WAL + busy_timeout + Lock)."""

    def __init__(self, db_path: Path | str) -> None:
        if str(db_path) != ":memory:":
            Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # busy_timeout: gdy inny PROCES (np. drzwi GitHub) trzyma zapis, poczekaj zamiast
        # natychmiastowego SQLITE_BUSY. WAL: lepsza współbieżność czytelnik/zapisujący na
        # pliku dzielonym przez procesy. Na ``:memory:`` WAL jest no-op — nieszkodliwe.
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.Lock()
        with self._lock:
            for stmt in _SCHEMA:
                self._conn.execute(stmt)
            self._conn.commit()

    def exists(self, source: str, external_id: str, kind: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM events WHERE source=? AND external_id=? AND kind=? LIMIT 1",
                (source, external_id, kind),
            ).fetchone()
        return row is not None

    def append(self, event: NewEvent) -> Event:
        with self._lock:
            # ON CONFLICT DO NOTHING: przy wyścigu dwóch procesów drugi INSERT to no-op,
            # a i tak zwracamy wiersz po kluczu (istniejący albo świeżo wstawiony) — dedup
            # egzekwuje baza, nie logika aplikacji.
            self._conn.execute(
                "INSERT INTO events(source, kind, external_id, actor, title, summary, url, "
                "occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source, external_id, kind) DO NOTHING",
                (
                    event.source,
                    event.kind,
                    event.external_id,
                    event.actor,
                    event.title,
                    event.summary,
                    event.url,
                    event.occurred_at.isoformat(),
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM events WHERE source=? AND external_id=? AND kind=?",
                (event.source, event.external_id, event.kind),
            ).fetchone()
        return _event(row)

    def read_since(
        self, after_id: int, *, source: str | None = None, limit: int = 50
    ) -> list[Event]:
        clause = " AND source=?" if source is not None else ""
        params: list[Any] = [after_id]
        if source is not None:
            params.append(source)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE id>?" + clause + " ORDER BY id ASC LIMIT ?",
                [*params, limit],
            ).fetchall()
        return [_event(r) for r in rows]

    def recent(self, *, source: str | None = None, limit: int = 20) -> list[Event]:
        clause = " WHERE source=?" if source is not None else ""
        params: list[Any] = [source] if source is not None else []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events" + clause + " ORDER BY id DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        return [_event(r) for r in rows]


def _parse_ts(value: Any) -> datetime:
    """Znacznik z bazy → aware UTC (ISO ``occurred_at`` albo ``CURRENT_TIMESTAMP`` bez strefy).

    ``occurred_at`` zapisujemy z offsetem (aware); ``ingested_at`` z ``CURRENT_TIMESTAMP`` jest
    w UTC, ale bez strefy — dołączamy ją, żeby oba pola były aware (spójne porównania, brak
    ``TypeError`` naive-vs-aware).
    """
    parsed = datetime.fromisoformat(str(value).replace(" ", "T"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _event(row: Any) -> Event:
    return Event(
        id=row["id"],
        source=row["source"],
        kind=row["kind"],
        external_id=row["external_id"],
        actor=row["actor"],
        title=row["title"],
        summary=row["summary"],
        url=row["url"],
        occurred_at=_parse_ts(row["occurred_at"]),
        ingested_at=_parse_ts(row["ingested_at"]),
    )
