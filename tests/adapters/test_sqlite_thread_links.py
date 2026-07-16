"""Testy adaptera SQLite mapowania wątek↔cel (SqliteThreadLinkStore, ADR 0024).

Baza ``:memory:``: link + odczyt dwukierunkowy (root po celu, cel po roocie), NADPISANIE roota
(upsert — przełączenie wątku po usunięciu roota), niezależność celów/kanałów/zespołów, brak wpisu.
"""
from __future__ import annotations

from workmate.adapters.outbound.sqlite_thread_links import SqliteThreadLinkStore


def _store() -> SqliteThreadLinkStore:
    return SqliteThreadLinkStore(":memory:")


def test_link_then_get_root_returns_root():
    store = _store()
    store.link("team-1", "chan-1", "pr", "12", "root-abc")
    assert store.get_root("team-1", "chan-1", "pr", "12") == "root-abc"


def test_get_target_returns_kind_and_number_by_root():
    """Kierunek wątek→GitHub: z roota odczytujemy cel (kind, number) — dwukierunkowość."""
    store = _store()
    store.link("team-1", "chan-1", "issue", "7", "root-xyz")
    assert store.get_target("team-1", "chan-1", "root-xyz") == ("issue", "7")


def test_relink_overwrites_root_for_same_target():
    """Ten sam cel z nowym rootem: upsert NADPISUJE (przełączenie wątku po usunięciu roota)."""
    store = _store()
    store.link("team-1", "chan-1", "pr", "12", "root-old")
    store.link("team-1", "chan-1", "pr", "12", "root-new")  # ten sam cel, nowy root
    assert store.get_root("team-1", "chan-1", "pr", "12") == "root-new"
    # Cel wskazuje teraz NOWY root; stary przestał być powiązany (nie 404-uje w kółko).
    assert store.get_target("team-1", "chan-1", "root-new") == ("pr", "12")
    assert store.get_target("team-1", "chan-1", "root-old") is None


def test_targets_are_independent_by_kind_and_number():
    store = _store()
    store.link("team-1", "chan-1", "pr", "12", "root-pr")
    store.link("team-1", "chan-1", "issue", "12", "root-issue")  # ten sam numer, inny rodzaj
    assert store.get_root("team-1", "chan-1", "pr", "12") == "root-pr"
    assert store.get_root("team-1", "chan-1", "issue", "12") == "root-issue"


def test_links_are_scoped_by_channel_and_team():
    store = _store()
    store.link("team-1", "chan-1", "pr", "12", "root-a")
    store.link("team-1", "chan-2", "pr", "12", "root-b")  # inny kanał
    store.link("team-2", "chan-1", "pr", "12", "root-c")  # inny zespół
    assert store.get_root("team-1", "chan-1", "pr", "12") == "root-a"
    assert store.get_root("team-1", "chan-2", "pr", "12") == "root-b"
    assert store.get_root("team-2", "chan-1", "pr", "12") == "root-c"


def test_missing_entry_returns_none():
    store = _store()
    assert store.get_root("team-1", "chan-1", "pr", "99") is None
    assert store.get_target("team-1", "chan-1", "root-nope") is None
