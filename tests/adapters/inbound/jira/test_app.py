"""Testy montażu wątkowania kanału w drzwiach Jira (_build_thread_links, ADR 0024 B2).

Bramka ``enable_channel_threading`` (wymaga ``enable_channel`` — pilnuje to ``validate``) decyduje,
czy notifier dostaje ``ThreadLinkStore`` (zdarzenia jednego zgłoszenia → jeden wątek), czy ``None``
(nowy root per zdarzenie). Test montażu, nie transportu — bez sieci i MSAL.
"""

from __future__ import annotations

from workmate.adapters.inbound.jira.app import _build_thread_links
from workmate.adapters.outbound.sqlite_thread_links import SqliteThreadLinkStore
from workmate.config import EventsSettings, TeamsPushSettings


def _channel_push(*, threading: bool) -> TeamsPushSettings:
    """Kompletny cel kanału (wymóg validate) z przełącznikiem wątkowania — respektuje zależność."""
    settings = TeamsPushSettings(
        enable_channel=True,
        client_id="c",
        tenant_id="t",
        team_id="team-1",
        channel_id="chan-1",
        enable_channel_threading=threading,
    )
    settings.validate()  # potwierdza, że konstruujemy prawidłową (niesprzeczną) konfigurację
    return settings


def test_returns_store_when_channel_threading_on(tmp_path):
    events_settings = EventsSettings(db_path=tmp_path / "events.db")
    store = _build_thread_links(events_settings, _channel_push(threading=True))
    assert isinstance(store, SqliteThreadLinkStore)
    # Zbudowany nad wskazaną bazą i realnie działa (zapis→odczyt celu).
    store.link("team-1", "chan-1", "jira", "WM-5", "root-1")
    assert store.get_root("team-1", "chan-1", "jira", "WM-5") == "root-1"


def test_returns_none_when_channel_threading_off(tmp_path):
    events_settings = EventsSettings(db_path=tmp_path / "events.db")
    assert _build_thread_links(events_settings, _channel_push(threading=False)) is None


def test_returns_none_when_push_disabled(tmp_path):
    # Ingest-only (żaden cel) → brak wątkowania, niezależnie od bazy zdarzeń.
    events_settings = EventsSettings(db_path=tmp_path / "events.db")
    assert _build_thread_links(events_settings, TeamsPushSettings()) is None
