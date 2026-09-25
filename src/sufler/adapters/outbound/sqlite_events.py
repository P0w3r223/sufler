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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sufler.core.domain.events import Event, NewEvent

_CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        source      TEXT NOT NULL,
        kind        TEXT NOT NULL,
        external_id TEXT NOT NULL,
        actor       TEXT NOT NULL DEFAULT '',
        title       TEXT NOT NULL DEFAULT '',
        summary     TEXT NOT NULL DEFAULT '',
        url         TEXT NOT NULL DEFAULT '',
        repo        TEXT NOT NULL DEFAULT '',
        project     TEXT NOT NULL DEFAULT '',
        occurred_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
        UNIQUE(source, external_id, kind)
    );
    """
# Kolumny repo/project (ADR 0028) dokładamy na starszych bazach przez ADD COLUMN — patrz
# ``_ensure_columns``. Dedup zostaje ``UNIQUE(source, external_id, kind)``; unikalność między
# repo daje złożenie repo w ``external_id`` (``composite_external_id``), nie zmiana constraintu.
_INDEXES = (
    # Kursor notifiera i podgląd po źródle idą po (source, id) — jeden indeks pokrywa oba.
    "CREATE INDEX IF NOT EXISTS idx_events_source_id ON events(source, id);",
    # Filtr po projekcie (ADR 0028) — atrybucja zdarzeń do projektu.
    "CREATE INDEX IF NOT EXISTS idx_events_project_id ON events(project, id);",
)


class SqliteEventStore:
    """``EventStore`` na SQLite — append-only, wieloprocesowy (WAL + busy_timeout + Lock)."""

    def __init__(self, db_path: Path | str) -> None:
        if str(db_path) != ":memory:":
            # ``expanduser`` musi objąć TAKŻE ``connect``: policzony wyłącznie na potrzeby
            # ``mkdir`` zakładał katalog rozwinięty (``/home/x/.sufler``), a bazę otwierał pod
            # literalnym ``~`` w katalogu roboczym procesu — dwa różne pliki pod jedną nazwą.
            db_path = Path(db_path).expanduser()
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # busy_timeout: gdy inny PROCES (np. drzwi GitHub) trzyma zapis, poczekaj zamiast
        # natychmiastowego SQLITE_BUSY. WAL: lepsza współbieżność czytelnik/zapisujący na
        # pliku dzielonym przez procesy. Na ``:memory:`` WAL jest no-op — nieszkodliwe.
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(_CREATE_TABLE)
            self._ensure_columns()
            for stmt in _INDEXES:
                self._conn.execute(stmt)
            self._conn.commit()

    def _ensure_columns(self) -> None:
        """Dołóż kolumny ``repo``/``project`` na bazach sprzed ADR 0028 (ADD COLUMN, backfill '').

        ``CREATE TABLE IF NOT EXISTS`` nie zmienia istniejącej tabeli, więc starsze pliki
        ``events.db`` nie miałyby tych kolumn — indeks po ``project`` by się wtedy wywrócił.
        Migracja jest idempotentna i bez rebuildu (append-only nienaruszone).
        """
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(events)")}
        for column in ("repo", "project"):
            if column not in existing:
                self._conn.execute(
                    f"ALTER TABLE events ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                )

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
                "repo, project, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source, external_id, kind) DO NOTHING",
                (
                    event.source,
                    event.kind,
                    event.external_id,
                    event.actor,
                    event.title,
                    event.summary,
                    event.url,
                    event.repo,
                    event.project,
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
        self,
        after_id: int,
        *,
        source: str | None = None,
        project: str | None = None,
        limit: int = 50,
    ) -> list[Event]:
        clauses = ["id>?"]
        params: list[Any] = [after_id]
        if source is not None:
            clauses.append("source=?")
            params.append(source)
        if project is not None:
            clauses.append("project=?")
            params.append(project)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE " + " AND ".join(clauses) + " ORDER BY id ASC LIMIT ?",
                [*params, limit],
            ).fetchall()
        return [_event(r) for r in rows]

    def recent(
        self, *, source: str | None = None, project: str | None = None, limit: int = 20
    ) -> list[Event]:
        return self._okno("id DESC", source=source, project=project, limit=limit)

    def recent_by_time(
        self, *, source: str | None = None, project: str | None = None, limit: int = 20
    ) -> list[Event]:
        """Okno po CZASIE ZDARZENIA. ``datetime(occurred_at)``, nie sam tekst kolumny.

        ``occurred_at`` zapisujemy przez ``isoformat()``, czyli z offsetem, jaki niosło zdarzenie —
        a porządek leksykalny napisów z RÓŻNYMI offsetami nie jest chronologiczny: sprawdzone,
        ``2026-09-07T13:30:00+02:00`` (11:30 UTC) wypada tekstowo PRZED ``…T12:00:00+00:00``,
        choć jest wcześniejsze. Dziś w produkcji wszystkie wiersze mają ``+00:00``, więc różnica
        byłaby niewidoczna — i właśnie dlatego trzeba ją domknąć teraz, a nie po pierwszym
        zdarzeniu z innym offsetem. ``datetime()`` normalizuje do UTC.

        Cena: sortowanie po WYRAŻENIU nie skorzysta z indeksu na kolumnie. Indeksu na
        ``occurred_at`` i tak nie ma, a okno jest ograniczone ``limit``.
        """
        return self._okno(
            "datetime(occurred_at) DESC, id DESC", source=source, project=project, limit=limit
        )

    def _okno(
        self,
        order_by: str,
        *,
        source: str | None,
        project: str | None,
        limit: int,
    ) -> list[Event]:
        """Okno zdarzeń z filtrami — jedno miejsce składania ``WHERE`` dla obu porządków.

        ``order_by`` jest wstawiane do SQL wprost, więc NIE MOŻE pochodzić od wołającego spoza tej
        klasy: obie wartości są literałami w metodach wyżej. Filtry idą parametrami, jak dotąd.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if source is not None:
            clauses.append("source=?")
            params.append(source)
        if project is not None:
            clauses.append("project=?")
            params.append(project)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM events{where} ORDER BY {order_by} LIMIT ?",
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
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


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
        repo=row["repo"],
        project=row["project"],
        occurred_at=_parse_ts(row["occurred_at"]),
        ingested_at=_parse_ts(row["ingested_at"]),
    )
