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

from sufler.core.domain.conversation import (
    Conversation,
    ConversationMessage,
    ConversationSearchHit,
    ConversationSummary,
)
from sufler.core.domain.pricing import TokenUsage


def _messages_ddl(table: str, *, if_not_exists: bool = False) -> str:
    """DDL tabeli wiadomości. FK ``conversation_id`` → ``conversations(id)`` (ADR 0012)
    egzekwuje na poziomie bazy „każda wiadomość należy do dokładnie jednego ISTNIEJĄCEGO
    wątku"; ``ON DELETE CASCADE`` domyka semantykę (usunięcie wątku zabiera jego
    wiadomości). Ta sama definicja służy tworzeniu tabeli dla nowej bazy oraz tabeli
    ``messages_new`` przy rebuildzie migracji FK — jedno źródło schematu. ``table`` to
    stała modułu (nie dane użytkownika), więc interpolacja nazwy jest bezpieczna.
    """
    guard = "IF NOT EXISTS " if if_not_exists else ""
    # Kolumny ``*_tokens`` (Design 2) — realne ``usage`` z odpowiedzi API, na wierszu
    # asystenta (NULL dla user/tool). ``token_estimate`` WYGASZONE (zawsze 0) — kolumnę
    # zostawiamy dla zgodności/uniknięcia rebuildu, ale nie liczymy już estymaty.
    return f"""
    CREATE TABLE {guard}{table} (
        id                          INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id             TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role                        TEXT NOT NULL,
        text                        TEXT NOT NULL,
        token_estimate              INTEGER NOT NULL,
        blocks_json                 TEXT,
        stop_reason                 TEXT,
        input_tokens                INTEGER,
        output_tokens               INTEGER,
        cache_read_input_tokens     INTEGER,
        cache_creation_input_tokens INTEGER,
        archived                    INTEGER NOT NULL DEFAULT 0,
        trust                       TEXT,
        created_at                  TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
    );
    """


# Tabela podsumowań kompaktowania (ADR 0014). Osobno od ``messages`` — podsumowanie to
# NIE tura rozmowy, tylko skrót zastępujący zarchiwizowane tury. ``status`` = ``active``
# / ``superseded`` (aktywne zawsze co najwyżej jedno na wątek). FK + CASCADE jak w
# ``messages``: usunięcie wątku zabiera jego podsumowania. Kolumny ``*_tokens`` to koszt
# wywołania modelu podsumowującego (Design 2, NULL gdy nie zmierzono).
_SUMMARIES_DDL = """
    CREATE TABLE IF NOT EXISTS conversation_summaries (
        id                          INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id             TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        summary                     TEXT NOT NULL,
        covers_through_message_id   INTEGER NOT NULL,
        status                      TEXT NOT NULL DEFAULT 'active',
        input_tokens                INTEGER,
        output_tokens               INTEGER,
        cache_read_input_tokens     INTEGER,
        cache_creation_input_tokens INTEGER,
        created_at                  TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
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
        updated_at  TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
        tainted     INTEGER NOT NULL DEFAULT 0,
        first_tainted_at TEXT,
        taint_source     TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_conv_lookup ON conversations(channel, external_id, status);",
    _messages_ddl("messages", if_not_exists=True),
    "CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);",
    _SUMMARIES_DDL,
    "CREATE INDEX IF NOT EXISTS idx_summary_conv "
    "ON conversation_summaries(conversation_id, status);",
)

# Kolumny dodane w ADR 0011 (bezstratna pamięć). Migracja jest ADDYTYWNA: dla baz
# sprzed 0011 (bez tych kolumn) dokładamy je przez ALTER; istniejące wiersze mają
# w nich NULL i przy odczycie degradują do text-only. ``CREATE TABLE IF NOT EXISTS``
# nie dodaje kolumn do istniejącej tabeli, więc migracja jest konieczna osobno.
_MESSAGES_ADDED_COLUMNS = {
    "blocks_json": "TEXT",
    "stop_reason": "TEXT",
    # Design 2: realne ``usage`` per tura asystenta (addytywnie, jak kolumny z ADR 0011).
    "input_tokens": "INTEGER",
    "output_tokens": "INTEGER",
    "cache_read_input_tokens": "INTEGER",
    "cache_creation_input_tokens": "INTEGER",
    # ADR 0014: flaga zarchiwizowania tury przez kompaktowanie. DEFAULT 0 → istniejące
    # wiersze są „nie zarchiwizowane" (pełny replay), zanim padnie pierwsze kompaktowanie.
    "archived": "INTEGER NOT NULL DEFAULT 0",
    # ADR 0066: klasa POCHODZENIA tury użytkownika (T1 instrukcja / T2 dane). NULL na wierszach
    # sprzed 0066 i na turach asystenta/narzędzi — odczyt degraduje wtedy do T1, czyli do
    # zachowania dawnego. Klasa musi być TRWAŁA, bo replay z pamięci nie zna już nadawcy:
    # bez kolumny tura gościa wracałaby w kolejnych turach jako instrukcja.
    "trust": "TEXT",
}

# Kolumny skazy rozmowy (ADR 0066). Migracja addytywna wołana dla ``conversations`` — ten sam
# mechanizm co dla ``messages``, tyle że tabela rozmów dotąd go nie potrzebowała. Skaza siedzi
# NA DYSKU, nie w pamięci procesu: recreate kontenera nie może zgubić stanu eskalacji.
_CONVERSATIONS_ADDED_COLUMNS = {
    "tainted": "INTEGER NOT NULL DEFAULT 0",
    "first_tainted_at": "TEXT",
    "taint_source": "TEXT",
}

# Jawna lista kolumn messages (kolejność DDL) do ``INSERT ... SELECT`` przy rebuildzie
# migracji FK (ADR 0012). Jawne nazwy są odporne na RÓŻNĄ fizyczną kolejność kolumn
# (ALTER dokłada na końcu), więc ``SELECT *`` mieszałby kolumny; nazwana lista nie.
_MESSAGES_COLUMN_LIST = (
    "id, conversation_id, role, text, token_estimate, blocks_json, stop_reason, "
    "input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens, "
    "archived, trust, created_at"
)


class SqliteConversationStore:
    """``ConversationStore`` na SQLite; full-text przez FTS5 (fallback: ``LIKE``)."""

    def __init__(self, db_path: Path | str) -> None:
        if str(db_path) != ":memory:":
            # ``expanduser`` musi objąć TAKŻE ``connect``: policzony wyłącznie na potrzeby
            # ``mkdir`` zakładał katalog rozwinięty (``/home/x/.sufler``), a bazę otwierał pod
            # literalnym ``~`` w katalogu roboczym procesu — dwa różne pliki pod jedną nazwą.
            db_path = Path(db_path).expanduser()
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # busy_timeout: gdy inny PROCES (drugie drzwi) trzyma zapis, poczekaj zamiast
        # natychmiastowego SQLITE_BUSY → OperationalError. WAL: lepsza współbieżność
        # czytelnik/zapisujący dla bazy plikowej, bo drzwi agentowe (osobne procesy)
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
            self._add_missing_columns("conversations", _CONVERSATIONS_ADDED_COLUMNS)
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
        existing = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def active_conversation(self, channel: str, external_id: str) -> Conversation | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE channel=? AND external_id=? "
                "AND status='active' ORDER BY rowid DESC LIMIT 1",
                (channel, external_id),
            ).fetchone()
            if row is None:
                return None
            # Martwy SUM(usage) usunięty z TEJ ścieżki (najgorętszej — każda tura): rollover
            # czyta wyłącznie last_context_tokens, message_count i updated_at. usage=0 tu,
            # a realne usage żyje w ``get``/``list_conversations`` (podgląd historii CLI).
            last_ctx, _ = _last_assistant_tokens(self._conn, row["id"])
            count = _message_count(self._conn, row["id"])
        # ``last_input_tokens`` (trigger kompaktowania) czytamy przez ``get`` tuż przed
        # kompaktowaniem — nie tu, żeby nie płacić zapytania na każdej turze (domyślne 0).
        return _conversation(row, TokenUsage(), last_ctx, count)

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
        return _conversation(row, TokenUsage(), 0, 0)

    def close_conversation(self, conversation_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET status='closed', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (conversation_id,),
            )
            self._conn.commit()

    def append_message(
        self,
        conversation_id: str,
        role: str,
        text: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
        stop_reason: str | None = None,
        usage: TokenUsage | None = None,
        trust: str | None = None,
    ) -> ConversationMessage:
        blocks_json = json.dumps(blocks, ensure_ascii=False) if blocks is not None else None
        u = usage  # realne usage (Design 2) — tylko na turze asystenta; NULL inaczej
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages("
                "conversation_id, role, text, token_estimate, blocks_json, stop_reason, "
                "input_tokens, output_tokens, cache_read_input_tokens, "
                "cache_creation_input_tokens, trust) "
                "VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)",  # token_estimate WYGASZONE (0)
                (
                    conversation_id,
                    role,
                    text,
                    blocks_json,
                    stop_reason,
                    u.input_tokens if u else None,
                    u.output_tokens if u else None,
                    u.cache_read_input_tokens if u else None,
                    u.cache_creation_input_tokens if u else None,
                    trust,
                ),
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
            row = self._conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
        return _message(row)

    def mark_tainted(self, conversation_id: str, source: str) -> None:
        """Zapal skazę rozmowy (ADR 0066); ``WHERE tainted=0`` czyni to idempotentnym.

        Warunek w SQL, nie odczyt-i-zapis w Pythonie: dwoje drzwi na jednej rozmowie to osobne
        PROCESY nad tym samym plikiem, więc „sprawdź, potem zapisz" byłoby wyścigiem i mogłoby
        przestawić źródło pierwszego zapłonu.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET tainted=1, first_tainted_at=CURRENT_TIMESTAMP, "
                "taint_source=? WHERE id=? AND tainted=0",
                (source, conversation_id),
            )
            self._conn.commit()

    def messages(self, conversation_id: str) -> list[ConversationMessage]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return [_message(r) for r in rows]

    # --- Kompaktowanie (ADR 0014) -------------------------------------------------

    def get(self, conversation_id: str) -> Conversation | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
            if row is None:
                return None
            usage = _conversation_usage(self._conn, conversation_id)
            last_ctx, last_in = _last_assistant_tokens(self._conn, conversation_id)
            count = _message_count(self._conn, conversation_id)
        return _conversation(row, usage, last_ctx, count, last_input_tokens=last_in)

    def replay_messages(self, conversation_id: str) -> list[ConversationMessage]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND archived=0 ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return [_message(r) for r in rows]

    def archive_through(self, conversation_id: str, message_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE messages SET archived=1 WHERE conversation_id=? AND id<=?",
                (conversation_id, message_id),
            )
            self._conn.commit()

    def save_summary(
        self,
        conversation_id: str,
        summary: str,
        covers_through_message_id: int,
        *,
        usage: TokenUsage | None = None,
    ) -> ConversationSummary:
        u = usage
        with self._lock:
            # Aktywne zawsze co najwyżej JEDNO — poprzednie oznacz jako zastąpione.
            self._conn.execute(
                "UPDATE conversation_summaries SET status='superseded' "
                "WHERE conversation_id=? AND status='active'",
                (conversation_id,),
            )
            cur = self._conn.execute(
                "INSERT INTO conversation_summaries("
                "conversation_id, summary, covers_through_message_id, status, "
                "input_tokens, output_tokens, cache_read_input_tokens, "
                "cache_creation_input_tokens) "
                "VALUES (?, ?, ?, 'active', ?, ?, ?, ?)",
                (
                    conversation_id,
                    summary,
                    covers_through_message_id,
                    u.input_tokens if u else None,
                    u.output_tokens if u else None,
                    u.cache_read_input_tokens if u else None,
                    u.cache_creation_input_tokens if u else None,
                ),
            )
            sid = cur.lastrowid
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM conversation_summaries WHERE id=?", (sid,)
            ).fetchone()
        return _summary(row)

    def active_summary(self, conversation_id: str) -> ConversationSummary | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversation_summaries "
                "WHERE conversation_id=? AND status='active' ORDER BY id DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return _summary(row)

    def list_conversations(
        self, *, channel: str | None = None, external_id: str | None = None, limit: int = 50
    ) -> list[Conversation]:
        # Realne usage + kontekst ostatniej tury liczymy per rozmowa (Design 2), pod jednym
        # zamkiem; ``updated_at`` (TEXT ISO) sortuje leksykograficznie = chronologicznie,
        # ``rowid`` rozstrzyga remisy przy równym znaczniku. ``limit`` chroni przed
        # nieograniczonym wypisem, więc pętla po (≤limit) rozmowach jest tania.
        #
        # Oba filtry idą do WHERE, nie do wołającego, i to jest tu rzecz nieoczywista: pętla
        # niżej robi trzy zapytania na wiersz, więc filtrowanie po zwróceniu okna kazałoby
        # policzyć koszt rozmów, które zaraz odpadną (okno 200 = 600 zapytań na 10 pozycji),
        # a przy okazji myliło „wątek bez historii" z „historia poza oknem".
        warunki = []
        params: list[Any] = []
        if channel is not None:
            warunki.append("channel=?")
            params.append(channel)
        if external_id is not None:
            warunki.append("external_id=?")
            params.append(external_id)
        clause = (" WHERE " + " AND ".join(warunki)) if warunki else ""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM conversations" + clause + " "
                "ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
            return [
                _conversation(
                    row,
                    _conversation_usage(self._conn, row["id"]),
                    _last_assistant_tokens(self._conn, row["id"])[0],
                    _message_count(self._conn, row["id"]),
                )
                for row in rows
            ]

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


def _conversation(
    row: Any,
    usage: TokenUsage,
    last_context_tokens: int,
    message_count: int,
    last_input_tokens: int = 0,
) -> Conversation:
    return Conversation(
        id=row["id"],
        channel=row["channel"],
        external_id=row["external_id"],
        status=row["status"],
        usage=usage,
        last_context_tokens=last_context_tokens,
        last_input_tokens=last_input_tokens,
        message_count=message_count,
        created_at=_parse_ts(row["created_at"]),
        updated_at=_parse_ts(row["updated_at"]),
        tainted=bool(row["tainted"]),
        taint_source=row["taint_source"] or "",
    )


def _conversation_usage(conn: Any, conv_id: str) -> TokenUsage:
    """Zsumowane realne usage rozmowy (Design 2) — wołane POD zamkiem magazynu."""
    r = conn.execute(
        "SELECT COALESCE(SUM(input_tokens),0) AS i, COALESCE(SUM(output_tokens),0) AS o, "
        "COALESCE(SUM(cache_read_input_tokens),0) AS cr, "
        "COALESCE(SUM(cache_creation_input_tokens),0) AS cc "
        "FROM messages WHERE conversation_id=?",
        (conv_id,),
    ).fetchone()
    return TokenUsage(
        input_tokens=int(r["i"]),
        output_tokens=int(r["o"]),
        cache_read_input_tokens=int(r["cr"]),
        cache_creation_input_tokens=int(r["cc"]),
    )


def _last_assistant_tokens(conn: Any, conv_id: str) -> tuple[int, int]:
    """(kontekst, wejście) OSTATNIEJ tury asystenta z JEDNEGO wiersza — rollover i kompaktowanie.

    ``kontekst`` = input+output+cache (≈ ile następna tura wyśle ponownie; trigger rolloveru),
    ``wejście`` = input+cache BEZ output (ile tokenów wejściowych model zobaczył; trigger
    kompaktowania, ADR 0014). Bierze najnowszy wiersz asystenta z zapisanym usage; ``(0, 0)``,
    gdy brak (świeży/legacy wątek) — wtedy ani rollover, ani kompaktowanie nie odpala. Jeden
    skan zamiast dwóch bliźniaczych SELECT-ów (identyczny ``WHERE/ORDER BY/LIMIT``).
    """
    r = conn.execute(
        "SELECT input_tokens AS i, output_tokens AS o, cache_read_input_tokens AS cr, "
        "cache_creation_input_tokens AS cc FROM messages "
        "WHERE conversation_id=? AND role='assistant' AND input_tokens IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (conv_id,),
    ).fetchone()
    if r is None:
        return 0, 0
    i, o, cr, cc = (r["i"] or 0), (r["o"] or 0), (r["cr"] or 0), (r["cc"] or 0)
    return i + o + cr + cc, i + cr + cc


def _message_count(conn: Any, conv_id: str) -> int:
    """Liczba tur rozmowy — sygnał „niepusty" dla bramek idle/``/nowa`` (POD zamkiem)."""
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id=?", (conv_id,)
        ).fetchone()[0]
    )


def _message(row: Any) -> ConversationMessage:
    raw_blocks = row["blocks_json"]
    return ConversationMessage(
        id=row["id"],
        conversation_id=row["conversation_id"],
        role=row["role"],
        text=row["text"],
        created_at=_parse_ts(row["created_at"]),
        blocks=json.loads(raw_blocks) if raw_blocks else None,
        stop_reason=row["stop_reason"],
        usage=_message_usage(row),
        archived=bool(row["archived"]),
        trust=row["trust"],
    )


def _summary(row: Any) -> ConversationSummary:
    """Rekord podsumowania z wiersza ``conversation_summaries`` (ADR 0014). ``usage`` z tych
    samych 4 kolumn tokenów co tura — reużywamy ``_message_usage``."""
    return ConversationSummary(
        id=row["id"],
        conversation_id=row["conversation_id"],
        summary=row["summary"],
        covers_through_message_id=row["covers_through_message_id"],
        created_at=_parse_ts(row["created_at"]),
        usage=_message_usage(row),
    )


def _message_usage(row: Any) -> TokenUsage | None:
    """TokenUsage z kolumn usage wiersza; ``None`` dla user/tool/legacy (brak usage)."""
    it, ot = row["input_tokens"], row["output_tokens"]
    cr, cc = row["cache_read_input_tokens"], row["cache_creation_input_tokens"]
    if it is None and ot is None and cr is None and cc is None:
        return None
    return TokenUsage(
        input_tokens=it or 0,
        output_tokens=ot or 0,
        cache_read_input_tokens=cr or 0,
        cache_creation_input_tokens=cc or 0,
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
