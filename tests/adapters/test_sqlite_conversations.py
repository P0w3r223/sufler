"""Testy magazynu rozmów SQLite (``SqliteConversationStore``, ADR 0010).

Prawdziwy SQLite w pamięci (``:memory:``) — bez sieci, bez plików w repo. Sprawdzamy
zapis/odczyt tur, sumę tokenów aktywnej rozmowy, domknięcie (znika z „aktywnych",
zostaje w archiwum) oraz wyszukiwanie po treści (FTS5 albo fallback LIKE — test jest
niezależny od trybu).
"""
from __future__ import annotations

from workmate.adapters.outbound.sqlite_conversations import SqliteConversationStore


def _store() -> SqliteConversationStore:
    return SqliteConversationStore(":memory:")


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
