"""Testy I/O trwałego stanu pollera Teams (``state.load`` / ``state.save``).

Stan to plik JSON (watermark wątków + dedup ``replied``). Testujemy na ``tmp_path``,
bez sieci: brak pliku → pusty stan, round-trip zachowuje słownik, ``save`` tworzy
brakujący katalog nadrzędny, a zapis jest sformatowanym JSON-em (``indent=2``).
"""

from __future__ import annotations

import json
from pathlib import Path

from workmate.adapters.inbound.teams_graph import state


def test_load_missing_file_returns_empty_state(tmp_path: Path):
    assert state.load(tmp_path / "nie-ma.json") == {}


def test_save_then_load_round_trips_dict(tmp_path: Path):
    path = tmp_path / "state.json"
    original = {
        "channel-a": {"watermark": "2024-01-01T11:00:00Z", "replied": ["m1", "m2"]},
        "channel-b": {"watermark": "2024-01-02T09:30:00Z", "replied": []},
    }

    state.save(path, original)

    assert state.load(path) == original


def test_save_creates_missing_parent_directory(tmp_path: Path):
    path = tmp_path / "podkatalog" / "glebiej" / "state.json"

    state.save(path, {"k": "v"})

    assert path.exists()
    assert state.load(path) == {"k": "v"}


def test_save_writes_indented_json(tmp_path: Path):
    path = tmp_path / "state.json"

    state.save(path, {"channel-a": {"replied": ["m1"]}})

    expected = json.dumps({"channel-a": {"replied": ["m1"]}}, indent=2)
    assert path.read_text() == expected


def test_save_overwrites_existing_file(tmp_path: Path):
    path = tmp_path / "state.json"

    state.save(path, {"channel-a": {"watermark": "old"}})
    state.save(path, {"channel-b": {"watermark": "new"}})

    assert state.load(path) == {"channel-b": {"watermark": "new"}}
