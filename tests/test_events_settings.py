"""Testy konfiguracji magazynu zdarzeń (EventsSettings, ADR 0019) — from_env + nadpisanie env."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_validate_rejects_events_db_inside_data_dir(tmp_path):
    """Inwariant: zdarzenia przychodzą z drzwi, więc plik nie może wylądować tam, gdzie rdzeń
    indeksuje notatki. Bliźniaczy do ``test_workspace_settings`` — ta sama granica, inny magazyn."""
    data = tmp_path / "data"
    settings = EventsSettings(db_path=data / "notes" / "events.db")

    with pytest.raises(ValueError, match="leży w katalogu danych"):
        settings.validate(data_dir=data)


def test_validate_accepts_events_db_next_to_data_dir(tmp_path):
    data = tmp_path / "data"
    settings = EventsSettings(db_path=tmp_path / "state" / "events.db")

    settings.validate(data_dir=data)  # bez wyjątku


def test_validate_wymaga_jawnego_data_dir_zamiast_cicho_zawezac():
    """Regresja szwu: drzwi GitHub — jedyny proces, który te zdarzenia ZAPISUJE — wołały
    ``validate()`` bez argumentu i cały inwariant leciał w próżnię, bo pominięcie wyglądało
    tak samo jak świadome ``None``. Brak wartości domyślnej czyni z tego błąd wywołania."""
    with pytest.raises(TypeError):
        EventsSettings().validate()  # type: ignore[call-arg]
