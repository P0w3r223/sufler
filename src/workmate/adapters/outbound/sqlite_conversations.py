"""Magazyn rozmów oparty o SQLite (implementacja ``ConversationStore``, ADR 0010).

Dlaczego SQLite: rozmowy to dane operacyjne (nie baza wiedzy), a wymóg to wydajne
przechowanie WIELU rozmów i łatwe wyszukiwanie starych. ``sqlite3`` jest w stdlib
(zero zależności), a FTS5 daje szybki full-text search. Gdy FTS5 nie jest wkompilowane
w danym buildzie Pythona, degradujemy łagodnie do wyszukiwania ``LIKE`` (wolniejsze,
ale działa) — wykrywane raz przy starcie.

Współbieżność: drzwi async wołają magazyn w wątkach puli (``run_in_executor``),
więc jedno połączenie z ``check_same_thread=False`` + ``Lock`` wokół operacji.
Znaczniki czasu nadaje baza (``CURRENT_TIMESTAMP``), identyfikatory — ``uuid4``:
rdzeń nie woła zegara ani losowości.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from workmate.core.domain.conversation import (
    Conversation,
    ConversationMessage,
    ConversationSearchHit,
)


def _messages_ddl(table: str, *, if_not_exists: bool = False) -> str:
    """DDL tabeli wiadomości. FK ``conversation_id`` → ``conversations(id)`` (ADR 0012)
    egzekwuje na poziomie bazy „każda wiadomość należy do dokładnie jednego ISTNIEJĄCEGO
    wątku"; ``ON DELETE CASCADE`` domyka semantykę (usunięcie wątku zabiera jego
    wiadomości). Ta sama definicja służy tworzeniu tabeli dla nowej bazy oraz tabeli
    ``messages_new`` przy rebuildzie migracji FK — jedno źródło schematu. ``table`` to
    stała modułu (nie dane użytkownika), więc interpolacja nazwy jest bezpieczna.
    """
    guard = "IF NOT EXISTS " if if_not_exists else ""
    return f"""
    CREATE TABLE {guard}{table} (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role            TEXT NOT NULL,
        text            TEXT NOT NULL,
        token_estimate  INTEGER NOT NULL,
        blocks_json     TEXT,
        stop_reason     TEXT,
        created_at      TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
    );
    """


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id          TEXT PRIMARY KEY,
        channel     TEXT NOT NULL,
        external_id TEXT NOT NULL,
        status      TEXT NOT NULL,
        created_at  TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
        updated_at  TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_conv_lookup ON conversations(channel, external_id, status);",
    _messages_ddl("messages", if_not_exists=True),
    "CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);",
)

# Kolumny dodane w ADR 0011 (bezstratna pamięć). Migracja jest ADDYTYWNA: dla baz
# sprzed 0011 (bez tych kolumn) dokładamy je przez ALTER; istniejące wiersze mają
# w nich NULL i przy odczycie degradują do text-only. ``CREATE TABLE IF NOT EXISTS``
# nie dodaje kolumn do istniejącej tabeli, więc migracja jest konieczna osobno.
_MESSAGES_ADDED_COLUMNS = {"blocks_json": "TEXT", "stop_reason": "TEXT"}

# Jawna lista kolumn messages (kolejność DDL) do ``INSERT ... SELECT`` przy rebuildzie
# migracji FK (ADR 0012). Jawne nazwy są odporne na RÓŻNĄ fizyczną kolejność kolumn:
# baza sprzed 0011 ma ``created_at`` przed dołożonymi (ALTER) ``blocks_json``/``stop_reason``,
# nowa baza — po nich. ``SELECT *`` mieszałby wtedy kolumny; nazwana lista nie.
_MESSAGES_COLUMN_LIST = (
    "id, conversation_id, role, text, token_estimate, blocks_json, stop_reason, created_at"
)


class SqliteConversationStore:
    """``ConversationStore`` na SQLite; full-text przez FTS5 (fallback: ``LIKE``)."""

    def __init__(self, db_path: Path | str) -> None:
        if str(db_path) != ":memory:":
            Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # busy_timeout: gdy inny PROCES (drugie drzwi) trzyma zapis, poczekaj zamiast
        # natychmiastowego SQLITE_BUSY → OperationalError. WAL: lepsza współbieżność
        # czytelnik/zapisujący dla bazy plikowej, bo Teams i Telegram (osobne procesy)
        # domyślnie dzielą ten sam plik. Na ``:memory:`` WAL jest no-op — nieszkodliwe.
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.Lock()
        self._fts = self._init_schema()

    def _init_schema(self) -> bool:
        with self._lock:
            for stmt in _SCHEMA:
                self._conn.execute(stmt)
            self._add_missing_columns("messages", _MESSAGES_ADDED_COLUMNS)
            migrated_fk = self._migrate_messages_add_fk()
            fts = True
            try:
                self._conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts "
                    "USING fts5(text, content='messages', content_rowid='id');"
                )
            except sqlite3.OperationalError:
                fts = False  # build Pythona bez FTS5 — użyjemy LIKE
            # Migracja FK przebudowuje tabelę messages (drop→rename), więc zewnętrzny
            # indeks FTS trzeba przeliczyć od zera z aktualnej treści.
            if fts and migrated_fk:
                self._conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
            self._conn.commit()
            # Egzekwuj FK dopiero PO migracji: rebuild (DROP/RENAME) wymaga foreign_keys
            # OFF (stan domyślny po connect). Od teraz każdy zapis do messages jest
            # sprawdzany — wiadomość do nieistniejącego wątku → IntegrityError (fail fast).
            self._conn.execute("PRAGMA foreign_keys = ON")
        return fts

    def _migrate_messages_add_fk(self) -> bool:
        """Dołóż FK ``messages.conversation_id`` → ``conversations(id)`` (ADR 0012).

        SQLite nie potrafi dodać więzu FK przez ``ALTER`` — trzeba przebudować tabelę
        (utwórz nową → skopiuj → usuń starą → zmień nazwę). Idempotentne: gdy FK już
        istnieje (nowa baza z ``_messages_ddl`` albo baza już zmigrowana), zwraca
        ``False`` i nie rusza danych. Uruchamiane przy ``foreign_keys = OFF`` (domyślny
        stan po connect; ON włączamy dopiero po migracji), więc DROP/RENAME są bezpieczne.
        Osierocone wiersze (bez istniejącej rozmowy) usuwamy PRZED założeniem więzu —
        inaczej rebuild zostawiłby dane łamiące FK. Zwraca, czy przebudowa zaszła.
        """
        has_fk = any(
            row["table"] == "conversations"
            for row in self._conn.execute("PRAGMA foreign_key_list(messages)")
        )
        if has_fk:
            return False
        # Indeks FTS jest ZEWNĘTRZNY wobec messages (content='messages') — zrzucamy go,
        # by DROP starej tabeli nie kolidował; _init_schema odtworzy go i przeliczy.
        self._conn.execute("DROP TABLE IF EXISTS messages_fts")
        self._conn.execute(
            "DELETE FROM messages WHERE conversation_id NOT IN (SELECT id FROM conversations)"
        )
        self._conn.execute(_messages_ddl("messages_new"))
        self._conn.execute(
            f"INSERT INTO messages_new ({_MESSAGES_COLUMN_LIST}) "
            f"SELECT {_MESSAGES_COLUMN_LIST} FROM messages"
        )
        self._conn.execute("DROP TABLE messages")
        self._conn.execute("ALTER TABLE messages_new RENAME TO messages")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id)"
        )
        return True

    def _add_missing_columns(self, table: str, columns: dict[str, str]) -> None:
        """Dołóż brakujące kolumny (ALTER ADD COLUMN) — migracja addytywna, ADR 0011.

        ``table``/``columns`` to stałe modułu (nie dane użytkownika), więc interpolacja
        nazwy jest bezpieczna. SQLite ADD COLUMN jest online i niedestrukcyjne.
        """
        existing = {
            row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")
        }
        for name, decl in columns.items():
            if name not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def active_conversation(
        self, channel: str, external_id: str
    ) -> Conversation | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE channel=? AND external_id=? "
                "AND status='active' ORDER BY rowid DESC LIMIT 1",
                (channel, external_id),
            ).fetchone()
            if row is None:
                return None
            total = self._conn.execute(
                "SELECT COALESCE(SUM(token_estimate), 0) FROM messages "
                "WHERE conversation_id=?",
                (row["id"],),
            ).fetchone()[0]
        return _conversation(row, int(total))

    def open_conversation(self, channel: str, external_id: str) -> Conversation:
        conv_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversations(id, channel, external_id, status) "
                "VALUES (?, ?, ?, 'active')",
                (conv_id, channel, external_id),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE id=?", (conv_id,)
            ).fetchone()
        return _conversation(row, 0)

    def close_conversation(self, conversation_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET status='closed', updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?",
                (conversation_id,),
            )
            self._conn.commit()

    def append_message(
        self,
        conversation_id: str,
        role: str,
        text: str,
        token_estimate: int,
        *,
        blocks: list[dict[str, Any]] | None = None,
        stop_reason: str | None = None,
    ) -> ConversationMessage:
        blocks_json = (
            json.dumps(blocks, ensure_ascii=False) if blocks is not None else None
        )
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages("
                "conversation_id, role, text, token_estimate, blocks_json, stop_reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (conversation_id, role, text, token_estimate, blocks_json, stop_reason),
            )
            msg_id = cur.lastrowid
            # Tury narzędziowe mają pusty ``text`` — poza indeksem FTS (nic do dopasowania).
            if self._fts and text:
                self._conn.execute(
                    "INSERT INTO messages_fts(rowid, text) VALUES (?, ?)",
                    (msg_id, text),
                )
            self._conn.execute(
                "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (conversation_id,),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM messages WHERE id=?", (msg_id,)
            ).fetchone()
        return _message(row)

    def messages(self, conversation_id: str) -> list[ConversationMessage]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return [_message(r) for r in rows]

    def list_conversations(
        self, *, channel: str | None = None, limit: int = 50
    ) -> list[Conversation]:
        # Sumę tokenów per rozmowa liczy skorelowane podzapytanie (jak w
        # ``active_conversation``); ``updated_at`` (TEXT ISO) sortuje leksykograficznie
        # = chronologicznie, ``rowid`` rozstrzyga remisy przy równym znaczniku.
        clause = " WHERE channel=?" if channel is not None else ""
        params: list[Any] = [channel] if channel is not None else []
        with self._lock:
            rows = self._conn.execute(
                "SELECT *, COALESCE("
                "(SELECT SUM(token_estimate) FROM messages m "
                "WHERE m.conversation_id = conversations.id), 0) AS token_total "
                "FROM conversations" + clause + " "
                "ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        return [_conversation(row, int(row["token_total"])) for row in rows]

    def search(
        self,
        query: str,
        *,
        channel: str | None = None,
        external_id: str | None = None,
        limit: int = 20,
    ) -> list[ConversationSearchHit]:
        query = query.strip()
        if not query:
            return []
        filters = ""
        params: list[Any] = []
        if channel is not None:
            filters += " AND c.channel = ?"
            params.append(channel)
        if external_id is not None:
            filters += " AND c.external_id = ?"
            params.append(external_id)
        with self._lock:
            if self._fts:
                return self._search_fts(query, filters, params, limit)
            return self._search_like(query, filters, params, limit)

    def _search_fts(
        self, query: str, filters: str, params: list[Any], limit: int
    ) -> list[ConversationSearchHit]:
        # Zapytanie jako fraza (cudzysłów) — neutralizuje operatory FTS w wejściu.
        match = '"' + query.replace('"', '""') + '"'
        sql = (
            "SELECT m.conversation_id AS conversation_id, c.channel AS channel, "
            "c.external_id AS external_id, m.role AS role, m.text AS text, "
            "m.created_at AS created_at, "
            "snippet(messages_fts, 0, '[', ']', '…', 12) AS snippet "
            "FROM messages_fts "
            "JOIN messages m ON m.id = messages_fts.rowid "
            "JOIN conversations c ON c.id = m.conversation_id "
            "WHERE messages_fts MATCH ?" + filters + " ORDER BY rank LIMIT ?"
        )
        rows = self._conn.execute(sql, [match, *params, limit]).fetchall()
        return [
            ConversationSearchHit(
                conversation_id=r["conversation_id"],
                channel=r["channel"],
                external_id=r["external_id"],
                role=r["role"],
                text=r["text"],
                snippet=r["snippet"],
                created_at=_parse_ts(r["created_at"]),
            )
            for r in rows
        ]

    def _search_like(
        self, query: str, filters: str, params: list[Any], limit: int
    ) -> list[ConversationSearchHit]:
        # Escapuj wieloznaczniki LIKE (\ % _), żeby wejście użytkownika nie działało
        # jak wzorzec — poprawność wyszukiwania (nie SQLi: zapytanie sparametryzowane).
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        sql = (
            "SELECT m.conversation_id AS conversation_id, c.channel AS channel, "
            "c.external_id AS external_id, m.role AS role, m.text AS text, "
            "m.created_at AS created_at "
            "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.text LIKE ? ESCAPE '\\'" + filters + " ORDER BY m.id DESC LIMIT ?"
        )
        rows = self._conn.execute(sql, [like, *params, limit]).fetchall()
        return [
            ConversationSearchHit(
                conversation_id=r["conversation_id"],
                channel=r["channel"],
                external_id=r["external_id"],
                role=r["role"],
                text=r["text"],
                snippet=_snippet(r["text"], query),
                created_at=_parse_ts(r["created_at"]),
            )
            for r in rows
        ]


def _parse_ts(value: Any) -> datetime:
    """Zamień znacznik SQLite (``YYYY-MM-DD HH:MM:SS``) na ``datetime``."""
    return datetime.fromisoformat(str(value).replace(" ", "T"))


def _conversation(row: Any, token_estimate: int) -> Conversation:
    return Conversation(
        id=row["id"],
        channel=row["channel"],
        external_id=row["external_id"],
        status=row["status"],
        token_estimate=token_estimate,
        created_at=_parse_ts(row["created_at"]),
        updated_at=_parse_ts(row["updated_at"]),
    )


def _message(row: Any) -> ConversationMessage:
    raw_blocks = row["blocks_json"]
    return ConversationMessage(
        id=row["id"],
        conversation_id=row["conversation_id"],
        role=row["role"],
        text=row["text"],
        token_estimate=row["token_estimate"],
        created_at=_parse_ts(row["created_at"]),
        blocks=json.loads(raw_blocks) if raw_blocks else None,
        stop_reason=row["stop_reason"],
    )


def _snippet(text: str, query: str, width: int = 80) -> str:
    """Fragment treści wokół pierwszego trafienia (fallback ``LIKE``, bez FTS)."""
    position = text.lower().find(query.lower())
    if position == -1:
        return text[:width] + ("…" if len(text) > width else "")
    start = max(0, position - width // 3)
    end = min(len(text), start + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"
