"""Kwarantanna na SQLite: zdarzenia niewysyłalne (ADR 0067 §2) i wiadomości porzucone (ADR 0069).

Dwie tabele SIOSTRZANE wobec ``events`` w tym samym pliku ``events.db`` (wolumen ``state``) — to
metadane dostawy, nie baza wiedzy. Ten sam wzorzec współbieżności co pozostałe magazyny SQLite
(``check_same_thread=False`` + ``Lock``, ``WAL`` + ``busy_timeout``); WAL dopuszcza drugie
połączenie do tego samego pliku obok ``SqliteEventStore``.

``dead_letters`` (WYJŚCIE, notifier) trzyma zdarzenie, którego nie udało się wysłać do Teams;
``inbound_dead_letters`` (WEJŚCIE, poller Teams) — wiadomość od człowieka, której drzwi nie zdołały
obsłużyć w dopuszczonej liczbie prób. Osobne tabele, bo klucz jest innego rodzaju: zdarzenie ma
całkowity ``event_id`` z ``events.db``, a wiadomość Graph ma nieprzezroczysty identyfikator
tekstowy i sensu nabiera dopiero z kanałem i wątkiem. Wciśnięcie jednego w drugie kosztowałoby
``int(msg.id)`` na wartości, która tylko dziś wygląda na liczbę (ADR 0069 rozważa to wprost).

Oba wpisy są idempotentne (``INSERT OR IGNORE`` + ``UNIQUE``) — po kluczu ``(source, event_id)``
odpowiednio ``(door, message_id)``: powtórne przeniesienie tej samej pozycji (np. po restarcie,
gdy licznik prób ruszył od zera) nie duplikuje ani nie nadpisuje pierwszego powodu/czasu.
``failed_at`` to chwila KWARANTANNY (N-tej porażki), nie pierwszej — licznik prób nie zna czasu
pierwszej porażki.

Czego tu NIE MA: treści. Wpis niesie identyfikatory pozwalające pozycję ODNALEŹĆ (kanał, wątek,
id wiadomości, nadawca po AAD id) i powód porażki — nie tekst rozmówcy. Ta sama postawa co w
dzienniku audytu (ADR 0067 §1.3): akcje i ścieżki, nigdy treść. Trwałym źródłem treści zostaje
Teams.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workmate.adapters.outbound.sqlite_readonly import connect_readonly, select_recent

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

_INBOUND_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS inbound_dead_letters (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        door           TEXT NOT NULL,
        message_id     TEXT NOT NULL,
        channel        TEXT NOT NULL,
        thread_root_id TEXT NOT NULL,
        sender         TEXT NOT NULL,
        reason         TEXT NOT NULL,
        attempts       INTEGER NOT NULL,
        failed_at      TEXT NOT NULL,
        UNIQUE(door, message_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_inbound_dead_letters_door ON inbound_dead_letters(door);",
)

_INBOUND_COLUMNS = (
    "door",
    "message_id",
    "channel",
    "thread_root_id",
    "sender",
    "reason",
    "attempts",
    "failed_at",
)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _connect(db_path: Path | str, schema: tuple[str, ...]) -> sqlite3.Connection:
    """Otwórz połączenie w dyscyplinie wspólnej dla magazynów na ``events.db`` i załóż tabele."""
    if str(db_path) != ":memory:":
        # ``expanduser`` musi objąć TAKŻE ``connect``: policzony wyłącznie na potrzeby
        # ``mkdir`` zakładał katalog rozwinięty (``/home/x/.workmate``), a bazę otwierał pod
        # literalnym ``~`` w katalogu roboczym procesu — dwa różne pliki pod jedną nazwą.
        db_path = Path(db_path).expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    for stmt in schema:
        conn.execute(stmt)
    conn.commit()
    return conn


class SqliteDeadLetterStore:
    """``DeadLetterStore`` na SQLite — kwarantanna zdarzeń niewysyłalnych, idempotentna."""

    def __init__(self, db_path: Path | str, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self._conn = _connect(db_path, _SCHEMA)
        self._clock = clock
        self._lock = threading.Lock()

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


class SqliteInboundDeadLetterStore:
    """Kwarantanna wiadomości PORZUCONYCH przez drzwi wejściowe (ADR 0069), idempotentna.

    Siostra ``SqliteDeadLetterStore`` po drugiej stronie mostu: tamta trzyma to, czego nie
    daliśmy rady WYSŁAĆ, ta — to, czego nie daliśmy rady OBSŁUŻYĆ. Klucz idempotencji to
    ``(door, message_id)``, a ``message_id`` jest TEKSTEM, bo identyfikator wiadomości Graph
    jest nieprzezroczysty (dziś wygląda na liczbę, jutro nie musi).
    """

    def __init__(self, db_path: Path | str, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self._conn = _connect(db_path, _INBOUND_SCHEMA)
        self._clock = clock
        self._lock = threading.Lock()

    def record(
        self,
        *,
        door: str,
        message_id: str,
        channel: str,
        thread_root_id: str,
        sender: str,
        reason: str,
        attempts: int,
    ) -> None:
        """Zapisz porzuconą wiadomość do kwarantanny. Idempotentny po ``(door, message_id)``."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO inbound_dead_letters"
                "(door, message_id, channel, thread_root_id, sender, reason, attempts, failed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    door,
                    message_id,
                    channel,
                    thread_root_id,
                    sender,
                    reason[:_MAX_REASON_CHARS],
                    attempts,
                    self._clock().isoformat(),
                ),
            )
            self._conn.commit()

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        """Ostatnie porzucone wiadomości (najnowsze pierwsze) — do diagnozy i odtworzenia."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT "
                + ", ".join(_INBOUND_COLUMNS)
                + " FROM inbound_dead_letters ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{col: row[col] for col in _INBOUND_COLUMNS} for row in rows]


class SqliteDeadLetterReader:
    """Odczyt OBU kwarantann jednym połączeniem tylko-do-odczytu (ADR 0069 R2).

    Osobna klasa od pisarzy, a nie metoda w nich, bo ma inne prawa i inny cykl życia: narzędzie
    operatora otwiera bazę ``mode=ro`` i NIE zakłada schematu — pisarz robi odwrotnie. Jeden
    czytelnik na oba stoły, bo leżą w tym samym pliku (``events.db``) i operator pyta o nie
    w jednym przebiegu.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._conn = connect_readonly(db_path)
        self._lock = threading.Lock()

    def outbound(
        self,
        *,
        source: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Zdarzenia niewysłane do Teams (``dead_letters``); ``source`` to źródło zdarzenia."""
        with self._lock:
            return select_recent(
                self._conn,
                table="dead_letters",
                columns=_COLUMNS,
                time_column="failed_at",
                source_column="source",
                source=source,
                since=since,
                until=until,
                limit=limit,
            )

    def inbound(
        self,
        *,
        source: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Wiadomości porzucone przez drzwi (``inbound_dead_letters``); ``source`` to drzwi."""
        with self._lock:
            return select_recent(
                self._conn,
                table="inbound_dead_letters",
                columns=_INBOUND_COLUMNS,
                time_column="failed_at",
                source_column="door",
                source=source,
                since=since,
                until=until,
                limit=limit,
            )
