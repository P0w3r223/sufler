"""Testy I/O trwałego stanu pollera Jira (``state.load`` / ``state.save``).

Stan to plik JSON (watermark ``issues_since`` + kursor notifiera). Testujemy na ``tmp_path``,
bez sieci. Poza round-tripem sprawdzamy dwa niezmienniki odporności na ``docker stop``/reboot
(R1): zapis jest ATOMOWY (temp + ``os.replace``, więc przerwany zapis NIE ucina pliku), a
odczyt jest TOLERANCYJNY (uszkodzony plik → pusty stan + ostrzeżenie, nie wywrócenie procesu).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workmate.adapters.inbound.jira import state


def test_load_missing_file_returns_empty_state(tmp_path: Path):
    assert state.load(tmp_path / "nie-ma.json") == {}


def test_save_then_load_round_trips_dict(tmp_path: Path):
    path = tmp_path / "jira_state.json"
    original = {"issues_since": "2024-01-01T11:00:00Z", "notify_cursor": 42}

    state.save(path, original)

    assert state.load(path) == original


def test_save_creates_missing_parent_directory(tmp_path: Path):
    path = tmp_path / "podkatalog" / "glebiej" / "jira_state.json"

    state.save(path, {"notify_cursor": 1})

    assert path.exists()
    assert state.load(path) == {"notify_cursor": 1}


def test_save_overwrites_existing_file(tmp_path: Path):
    path = tmp_path / "jira_state.json"

    state.save(path, {"notify_cursor": 1})
    state.save(path, {"notify_cursor": 2})

    assert state.load(path) == {"notify_cursor": 2}


def test_load_corrupted_json_returns_empty_and_warns(tmp_path: Path, caplog):
    path = tmp_path / "jira_state.json"
    path.write_text('{ "issues_since": ', encoding="utf-8")  # ucięty przez stop w trakcie zapisu

    with caplog.at_level("WARNING"):
        result = state.load(path)

    assert result == {}
    assert any("nieczytelny" in rec.message for rec in caplog.records)


def test_load_non_dict_returns_empty(tmp_path: Path):
    path = tmp_path / "jira_state.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    assert state.load(path) == {}


def test_save_leaves_original_intact_if_replace_fails(tmp_path: Path, monkeypatch):
    """Awaria w chwili podmiany NIE może uszkodzić istniejącego stanu — sedno R1."""
    path = tmp_path / "jira_state.json"
    state.save(path, {"notify_cursor": 1})

    def boom(src: str, dst: str) -> None:
        raise OSError("symulacja ubicia w trakcie zapisu")

    monkeypatch.setattr(state.os, "replace", boom)
    with pytest.raises(OSError):
        state.save(path, {"notify_cursor": 2})

    assert json.loads(path.read_text(encoding="utf-8")) == {"notify_cursor": 1}


def test_save_leaves_no_tmp_file(tmp_path: Path):
    path = tmp_path / "jira_state.json"

    state.save(path, {"notify_cursor": 1})

    assert not path.with_suffix(path.suffix + ".tmp").exists()
