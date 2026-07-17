"""Testy konfiguracji magazynu zdarzeń (EventsSettings, ADR 0019) — from_env + nadpisanie env."""

from __future__ import annotations

from pathlib import Path

from workmate.config import EventsSettings


def test_default_db_path_outside_data(monkeypatch):
    monkeypatch.delenv("WORKMATE_EVENTS_DB", raising=False)
    settings = EventsSettings.from_env()
    # Domyślnie w katalogu domowym (~/.workmate), POZA repo i data/ — dane operacyjne.
    assert settings.db_path.name == "events.db"
    assert ".workmate" in settings.db_path.parts


def test_env_override(monkeypatch, tmp_path):
    target = tmp_path / "custom-events.db"
    monkeypatch.setenv("WORKMATE_EVENTS_DB", str(target))
    assert EventsSettings.from_env().db_path == Path(target)
