"""Testy magazynu rozmów SQLite (``SqliteConversationStore``, ADR 0010).

Prawdziwy SQLite w pamięci (``:memory:``) — bez sieci, bez plików w repo. Sprawdzamy
zapis/odczyt tur, sumę tokenów aktywnej rozmowy, domknięcie (znika z „aktywnych",
zostaje w archiwum) oraz wyszukiwanie po treści (FTS5 albo fallback LIKE — test jest
niezależny od trybu).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from workmate.adapters.outbound.sqlite_conversations import SqliteConversationStore

# Schemat sprzed 0012 (ADR 0011): messages BEZ FK conversation_id → conversations(id).
# Używany w testach migracji, by odtworzyć bazę, którą rebuild ma uszczelnić.
_PRE_FK_SCHEMA = """
    CREATE TABLE conversations (
        id TEXT PRIMARY KEY, channel TEXT NOT NULL, external_id TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
        updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
    );
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
        role TEXT NOT NULL, text TEXT NOT NULL, token_estimate INTEGER NOT NULL,
        blocks_json TEXT, stop_reason TEXT,
        created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
    );
"""


def _messages_has_fk(store: SqliteConversationStore) -> bool:
    return any(
        row["table"] == "conversations"
        for row in store._conn.execute("PRAGMA foreign_key_list(messages)")
    )


def _fts5_available() -> bool:
    """Czy build Pythona ma wkompilowane FTS5 (inaczej store degraduje do LIKE)."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def _store() -> SqliteConversationStore:
    return SqliteConversationStore(":memory:")


def _touch(store: SqliteConversationStore, conv_id: str, updated_at: str) -> None:
    """Ustaw jawny ``updated_at`` rozmowy do testów kolejności.

    Granularność ``CURRENT_TIMESTAMP`` to 1 s — rozmowy otwierane w jednym teście
    dostają identyczny znacznik, więc jawne wartości są jedynym deterministycznym
    sposobem sprawdzenia, że sortuje ``updated_at`` (a nie kolejność zapisu / rowid).
    """
    store._conn.execute(
        "UPDATE conversations SET updated_at=? WHERE id=?", (updated_at, conv_id)
    )
    store._conn.commit()


def test_append_and_read_messages_in_order():
    store = _store()
    conv = store.open_conversation("telegram", "chat1")

    store.append_message(conv.id, "user", "czesc", 1)
    store.append_message(conv.id, "assistant", "hej, w czym pomoc?", 5)

    msgs = store.messages(conv.id)
    assert [(m.role, m.text) for m in msgs] == [
        ("user", "czesc"),
        ("assistant", "hej, w czym pomoc?"),
    ]


def test_active_conversation_sums_token_estimate():
    store = _store()
    conv = store.open_conversation("teams", "conv-9")
    store.append_message(conv.id, "user", "a", 3)
    store.append_message(conv.id, "assistant", "b", 4)

    active = store.active_conversation("teams", "conv-9")
    assert active is not None
    assert active.id == conv.id
    assert active.token_estimate == 7


def test_close_removes_from_active_but_keeps_history():
    store = _store()
    conv = store.open_conversation("telegram", "chat2")
    store.append_message(conv.id, "user", "raport budzetowy", 3)

    store.close_conversation(conv.id)

    assert store.active_conversation("telegram", "chat2") is None
    # Historia (i wyszukiwalność) zostają.
    assert len(store.messages(conv.id)) == 1


def test_open_conversation_generates_distinct_ids():
    store = _store()
    a = store.open_conversation("telegram", "chat3")
    b = store.open_conversation("telegram", "chat3")
    assert a.id != b.id


def test_search_finds_across_conversations_with_filters():
    store = _store()
    c1 = store.open_conversation("telegram", "chatA")
    c2 = store.open_conversation("teams", "convB")
    store.append_message(c1.id, "user", "kiedy raport dla mpwik", 4)
    store.append_message(c2.id, "assistant", "status projektu enerkom", 4)

    # Znajduje po słowie w dowolnej rozmowie.
    hits = store.search("raport")
    assert any("raport" in h.text for h in hits)
    assert all(h.snippet for h in hits)

    # Filtr po kanale zawęża wynik.
    only_teams = store.search("enerkom", channel="teams")
    assert len(only_teams) == 1
    assert only_teams[0].conversation_id == c2.id

    # Brak trafień → pusta lista (nie błąd).
    assert store.search("nieistniejace-slowo-xyz") == []


# --- Podgląd historii: list_conversations --------------------------------------


def test_list_conversations_empty_db_returns_empty_list():
    """Pusta baza → pusta lista (nie błąd) — podgląd świeżej instalacji."""
    assert _store().list_conversations() == []


def test_list_conversations_sums_tokens_per_conversation():
    """Suma tokenów jest liczona PER rozmowa (skorelowane podzapytanie nie miesza tur)."""
    store = _store()
    a = store.open_conversation("telegram", "chatA")
    b = store.open_conversation("teams", "convB")
    store.append_message(a.id, "user", "x", 5)
    store.append_message(a.id, "assistant", "y", 7)
    store.append_message(b.id, "user", "z", 3)

    by_id = {c.id: c for c in store.list_conversations()}
    assert by_id[a.id].token_estimate == 12  # 5 + 7, tylko rozmowa A
    assert by_id[b.id].token_estimate == 3  # bez doliczania cudzych tur


def test_list_conversations_without_messages_reports_zero_tokens():
    """Świeżo otwarta rozmowa bez tur → suma 0 (COALESCE, nie NULL/błąd)."""
    store = _store()
    conv = store.open_conversation("cli", "pusta")

    result = store.list_conversations()
    assert [(c.id, c.token_estimate) for c in result] == [(conv.id, 0)]


def test_list_conversations_filters_by_channel():
    """Filtr kanału zawęża do jednego drzwi — pozostałe rozmowy pomijane."""
    store = _store()
    store.open_conversation("telegram", "chat1")
    teams = store.open_conversation("teams", "conv1")
    store.open_conversation("telegram", "chat2")

    result = store.list_conversations(channel="teams")
    assert [c.id for c in result] == [teams.id]


def test_list_conversations_orders_by_updated_at_newest_first():
    """Sortuje po ``updated_at`` malejąco — nawet gdy przeczy to kolejności zapisu."""
    store = _store()
    a = store.open_conversation("cli", "a")
    b = store.open_conversation("cli", "b")
    c = store.open_conversation("cli", "c")
    # Rozłączne znaczniki tak, by NAJNOWSZY updated_at miała NAJSTARSZA (rowid) rozmowa —
    # dowód, że o kolejności decyduje updated_at, a nie kolejność wstawiania.
    _touch(store, a.id, "2025-01-03 10:00:00")
    _touch(store, b.id, "2025-01-01 10:00:00")
    _touch(store, c.id, "2025-01-02 10:00:00")

    order = [conv.id for conv in store.list_conversations()]
    assert order == [a.id, c.id, b.id]


def test_list_conversations_respects_limit():
    """``limit`` chroni podgląd przed nieograniczonym wypisem długiej historii."""
    store = _store()
    for i in range(5):
        store.open_conversation("cli", f"chat{i}")

    assert len(store.list_conversations(limit=2)) == 2


def test_list_conversations_includes_active_and_closed():
    """Podgląd listuje CAŁE archiwum — domknięte rozmowy też (inaczej niż aktywny wątek)."""
    store = _store()
    active = store.open_conversation("cli", "live")
    closed = store.open_conversation("cli", "done")
    store.close_conversation(closed.id)

    ids = {c.id for c in store.list_conversations()}
    assert ids == {active.id, closed.id}


# --- Bezstratna pamięć bloków (ADR 0011) ---------------------------------------


def test_blocks_and_stop_reason_round_trip_verbatim():
    """Bloki (z ``signature`` thinking) i ``stop_reason`` wracają bajt-w-bajt."""
    store = _store()
    conv = store.open_conversation("telegram", "chat1")

    blocks = [
        {"type": "thinking", "thinking": "", "signature": "SIG=="},
        {"type": "text", "text": "cześć — ąęłń"},  # non-ASCII bez zmian (ensure_ascii=False)
        {"type": "tool_use", "id": "t1", "name": "search_notes", "input": {"q": "x"}},
    ]
    store.append_message(
        conv.id, "assistant", "cześć — ąęłń", 3, blocks=blocks, stop_reason="end_turn"
    )

    msg = store.messages(conv.id)[0]
    # Bloki identyczne co do treści I kolejności; sygnatura thinking nietknięta.
    assert msg.blocks == blocks
    assert [b["type"] for b in msg.blocks] == ["thinking", "text", "tool_use"]
    assert msg.blocks[0]["signature"] == "SIG=="
    assert msg.stop_reason == "end_turn"


def test_message_without_blocks_reads_as_text_only():
    """Ścieżka text-only (ADR 0010): brak bloków → ``blocks``/``stop_reason`` None."""
    store = _store()
    conv = store.open_conversation("telegram", "chat1")

    store.append_message(conv.id, "user", "pytanie", 2)

    msg = store.messages(conv.id)[0]
    assert msg.blocks is None
    assert msg.stop_reason is None
    assert msg.text == "pytanie"


def test_pre_0011_db_migrates_additively_and_reads_legacy_rows(tmp_path: Path):
    """Baza SPRZED 0011 (bez nowych kolumn): ALTER dokłada je, stare wiersze → text-only."""
    db_path = tmp_path / "legacy.db"
    # Schemat sprzed 0011: tabela messages BEZ blocks_json/stop_reason.
    legacy = sqlite3.connect(str(db_path))
    legacy.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY, channel TEXT NOT NULL, external_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
            role TEXT NOT NULL, text TEXT NOT NULL, token_estimate INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
        );
        INSERT INTO conversations(id, channel, external_id, status)
            VALUES ('c-old', 'telegram', 'chat1', 'active');
        INSERT INTO messages(conversation_id, role, text, token_estimate)
            VALUES ('c-old', 'assistant', 'stara odpowiedz', 4);
        """
    )
    legacy.commit()
    legacy.close()

    # Otwarcie store'a na tej bazie NIE rzuca — migracja addytywna dokłada kolumny.
    store = SqliteConversationStore(db_path)

    old = store.messages("c-old")[0]
    assert old.text == "stara odpowiedz"
    assert old.blocks is None  # stary wiersz degraduje do text-only
    assert old.stop_reason is None

    # Po migracji nowy zapis z blokami działa na tej samej (zmigrowanej) bazie.
    store.append_message(
        "c-old", "assistant", "nowa", 1,
        blocks=[{"type": "text", "text": "nowa"}], stop_reason="end_turn",
    )
    fresh = store.messages("c-old")[1]
    assert fresh.blocks == [{"type": "text", "text": "nowa"}]
    assert fresh.stop_reason == "end_turn"


# --- Wymuszona integralność wątku: FK messages → conversations (ADR 0012) -------


def test_new_db_enforces_fk_message_needs_existing_thread():
    """Nowa baza ma FK od razu — zapis do nieistniejącego wątku to IntegrityError."""
    store = _store()
    assert _messages_has_fk(store)
    with pytest.raises(sqlite3.IntegrityError):
        store.append_message("brak-takiego-watku", "user", "x", 1)


def test_pre_0012_db_migrates_to_enforced_fk_preserving_data_and_fts(tmp_path: Path):
    """Baza sprzed 0012 (messages bez FK): rebuild zakłada FK, dane i FTS przeżywają."""
    db_path = tmp_path / "pre_fk.db"
    legacy = sqlite3.connect(str(db_path))
    legacy.executescript(
        _PRE_FK_SCHEMA
        + """
        INSERT INTO conversations(id, channel, external_id, status)
            VALUES ('c1', 'telegram', 'chat1', 'active');
        INSERT INTO messages(conversation_id, role, text, token_estimate)
            VALUES ('c1', 'user', 'stare pytanie o raport', 4);
        """
    )
    legacy.commit()
    legacy.close()

    store = SqliteConversationStore(db_path)

    # Więz założony przez migrację (rebuild), a dane zachowane co do treści.
    assert _messages_has_fk(store)
    assert store.messages("c1")[0].text == "stare pytanie o raport"
    # FTS przeliczone po rebuildzie — indeks znajduje zmigrowaną treść.
    assert any("raport" in hit.text for hit in store.search("raport"))
    # Integralność faktycznie egzekwowana na zmigrowanej bazie.
    with pytest.raises(sqlite3.IntegrityError):
        store.append_message("ghost", "user", "x", 1)


def test_migration_drops_orphan_messages_before_applying_fk(tmp_path: Path):
    """Wiersz bez istniejącej rozmowy blokowałby FK — migracja usuwa go przed więzem."""
    db_path = tmp_path / "orphans.db"
    legacy = sqlite3.connect(str(db_path))
    legacy.executescript(
        _PRE_FK_SCHEMA
        + """
        INSERT INTO conversations(id, channel, external_id, status)
            VALUES ('c1', 'telegram', 'chat1', 'active');
        INSERT INTO messages(conversation_id, role, text, token_estimate)
            VALUES ('c1', 'user', 'prawidlowa', 2);
        INSERT INTO messages(conversation_id, role, text, token_estimate)
            VALUES ('ghost', 'user', 'osierocona', 2);
        """
    )
    legacy.commit()
    legacy.close()

    store = SqliteConversationStore(db_path)  # nie rzuca — sieroty usunięte przed FK

    assert _messages_has_fk(store)
    assert store.messages("c1")[0].text == "prawidlowa"
    orphan_count = store._conn.execute(
        "SELECT COUNT(*) FROM messages WHERE conversation_id = 'ghost'"
    ).fetchone()[0]
    assert orphan_count == 0


@pytest.mark.skipif(not _fts5_available(), reason="build Pythona bez FTS5")
def test_pre_0012_db_with_existing_fts_migrates_and_keeps_search(tmp_path: Path):
    """Realna baza sprzed 0012 MA już indeks FTS — rebuild dropuje go i odbudowuje.

    To ścieżka, którą przechodzi KAŻDA prawdziwa baza 0011 (odróżnia się od pustej-FTS
    bazy z pozostałych testów migracji): migracja FK musi zrzucić istniejący
    external-content ``messages_fts`` przed DROP tabeli i przeliczyć indeks po rebuildzie.
    """
    db_path = tmp_path / "pre_fk_fts.db"
    legacy = sqlite3.connect(str(db_path))
    legacy.executescript(
        _PRE_FK_SCHEMA
        + """
        CREATE VIRTUAL TABLE messages_fts
            USING fts5(text, content='messages', content_rowid='id');
        INSERT INTO conversations(id, channel, external_id, status)
            VALUES ('c1', 'telegram', 'chat1', 'active');
        INSERT INTO messages(conversation_id, role, text, token_estimate)
            VALUES ('c1', 'user', 'ustalenia z mpwik o raporcie', 5);
        INSERT INTO messages_fts(rowid, text) SELECT id, text FROM messages;
        """
    )
    legacy.commit()
    legacy.close()

    store = SqliteConversationStore(db_path)

    assert _messages_has_fk(store)
    assert store._fts  # FTS odtworzone po rebuildzie (nie fallback LIKE)
    # Indeks przeliczony — znajduje treść zaindeksowaną jeszcze przed migracją.
    assert any("mpwik" in hit.text for hit in store.search("mpwik"))
    # Nowy zapis też się indeksuje na zmigrowanej bazie.
    store.append_message("c1", "assistant", "odpowiedz o enerkom", 4)
    assert any("enerkom" in hit.text for hit in store.search("enerkom"))


def test_reopening_migrated_db_is_idempotent(tmp_path: Path):
    """Drugie otwarcie już zmigrowanej bazy nie rusza danych (migracja jednorazowa)."""
    db_path = tmp_path / "twice.db"
    legacy = sqlite3.connect(str(db_path))
    legacy.executescript(
        _PRE_FK_SCHEMA
        + """
        INSERT INTO conversations(id, channel, external_id, status)
            VALUES ('c1', 'telegram', 'chat1', 'active');
        INSERT INTO messages(conversation_id, role, text, token_estimate)
            VALUES ('c1', 'assistant', 'zachowana tresc', 3);
        """
    )
    legacy.commit()
    legacy.close()

    SqliteConversationStore(db_path)._conn.close()  # pierwsza migracja
    reopened = SqliteConversationStore(db_path)  # drugie otwarcie — FK już jest

    assert _messages_has_fk(reopened)
    assert reopened.messages("c1")[0].text == "zachowana tresc"
