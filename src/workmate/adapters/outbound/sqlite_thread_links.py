"""Mapowanie wątek Teams ↔ cel GitHub na SQLite (implementacja ``ThreadLinkStore``, ADR 0024).

OSOBNA tabela ``thread_links`` w TYM SAMYM pliku ``events.db`` co ``EventStore`` — wspólne miejsce
spotkania dwóch procesów drzwi (notifier w drzwiach GitHub tworzy/czyta linki; reaktywne drzwi
teams_graph czytają cel wątku), ale EventStore pozostaje NIETKNIĘTY (osobny adapter, osobne
połączenie, osobna tabela). Ten sam wzorzec współbieżności co ``SqliteEventStore``: jedno połączenie
``check_same_thread=False`` + ``Lock``, ``WAL`` + ``busy_timeout`` (drzwi to osobne PROCESY dzielące
plik). Jednoznaczność ``(team, channel, kind, number)``: jeden wątek na cel. ``link`` NADPISUJE root
(upsert) — do przełączenia wątku, gdy stary root usunięto w Teams. Gwarancja „jeden root na cel"
zakłada JEDEN proces notifiera (drzwi GitHub); dwa notifiery mogłyby utworzyć dwa rooty (jeden
osierocony) — analogicznie do inwariantu „jeden pisarz" auto-komentarza CI.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS thread_links (
        team_id       TEXT NOT NULL,
        channel_id    TEXT NOT NULL,
        target_kind   TEXT NOT NULL,
        target_number TEXT NOT NULL,
        root_id       TEXT NOT NULL,
        created_at    TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
        UNIQUE(team_id, channel_id, target_kind, target_number)
    );
    """,
    # Odczyt celu po roocie (kierunek wątek→GitHub) idzie po (team, channel, root_id).
    "CREATE INDEX IF NOT EXISTS idx_thread_links_root "
    "ON thread_links(team_id, channel_id, root_id);",
)


class SqliteThreadLinkStore:
    """``ThreadLinkStore`` na SQLite — dwukierunkowe mapowanie wątek↔cel, wieloprocesowe (WAL)."""

    def __init__(self, db_path: Path | str) -> None:
        if str(db_path) != ":memory:":
            # ``expanduser`` musi objąć TAKŻE ``connect``: policzony wyłącznie na potrzeby
            # ``mkdir`` zakładał katalog rozwinięty (``/home/x/.workmate``), a bazę otwierał pod
            # literalnym ``~`` w katalogu roboczym procesu — dwa różne pliki pod jedną nazwą.
            db_path = Path(db_path).expanduser()
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.Lock()
        with self._lock:
            for stmt in _SCHEMA:
                self._conn.execute(stmt)
            self._conn.commit()

    def get_root(
        self, team_id: str, channel_id: str, target_kind: str, target_number: str
    ) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT root_id FROM thread_links WHERE team_id=? AND channel_id=? "
                "AND target_kind=? AND target_number=?",
                (team_id, channel_id, target_kind, target_number),
            ).fetchone()
        return str(row["root_id"]) if row is not None else None

    def get_target(self, team_id: str, channel_id: str, root_id: str) -> tuple[str, str] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT target_kind, target_number FROM thread_links "
                "WHERE team_id=? AND channel_id=? AND root_id=?",
                (team_id, channel_id, root_id),
            ).fetchone()
        return (str(row["target_kind"]), str(row["target_number"])) if row else None

    def link(
        self,
        team_id: str,
        channel_id: str,
        target_kind: str,
        target_number: str,
        root_id: str,
    ) -> None:
        # ON CONFLICT DO UPDATE: NADPISUJEMY root celu. Normalna ścieżka woła ``link`` dopiero po
        # ``get_root``==None (brak konfliktu), więc to zwykły insert. Nadpisanie jest potrzebne dla
        # DEGRADACJI notifiera, gdy stary root został usunięty w Teams (ADR 0024): tworzy nowy root
        # i musi PRZEŁĄCZYĆ link, inaczej kolejne zdarzenia znów trafiałyby na 404 usuniętego roota.
        with self._lock:
            self._conn.execute(
                "INSERT INTO thread_links(team_id, channel_id, target_kind, target_number, "
                "root_id) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(team_id, channel_id, target_kind, target_number) "
                "DO UPDATE SET root_id=excluded.root_id, created_at=CURRENT_TIMESTAMP",
                (team_id, channel_id, target_kind, target_number, root_id),
            )
            self._conn.commit()
