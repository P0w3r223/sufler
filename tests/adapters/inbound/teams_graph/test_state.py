"""Testy I/O trwałego stanu pollera Teams (``state.load`` / ``state.save``).

Stan to plik JSON (watermark wątków + dedup ``replied``). Testujemy na ``tmp_path``,
bez sieci: brak pliku → pusty stan, round-trip zachowuje słownik, ``save`` tworzy
brakujący katalog nadrzędny, a zapis jest sformatowanym JSON-em (``indent=2``). Poza
round-tripem: zapis jest ATOMOWY (temp + ``os.replace``, przerwany zapis NIE ucina pliku),
a odczyt TOLERANCYJNY (uszkodzony plik → pusty stan + ostrzeżenie, nie wywrócenie procesu) —
te same niezmienniki R1 co ``github/state.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def test_load_corrupted_json_returns_empty_and_warns(tmp_path: Path, caplog):
    path = tmp_path / "state.json"
    path.write_text('{ "channel-a": ', encoding="utf-8")  # ucięty przez stop w trakcie zapisu

    with caplog.at_level("WARNING"):
        result = state.load(path)

    assert result == {}
    assert any("nieczytelny" in rec.message for rec in caplog.records)


def test_load_non_dict_returns_empty(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    assert state.load(path) == {}


def test_save_leaves_original_intact_if_replace_fails(tmp_path: Path, monkeypatch):
    """Awaria w chwili podmiany NIE może uszkodzić istniejącego stanu — sedno R1."""
    path = tmp_path / "state.json"
    state.save(path, {"channel-a": {"watermark": "old"}})

    def boom(src: str, dst: str) -> None:
        raise OSError("symulacja ubicia w trakcie zapisu")

    monkeypatch.setattr(state.os, "replace", boom)
    with pytest.raises(OSError):
        state.save(path, {"channel-a": {"watermark": "new"}})

    assert json.loads(path.read_text(encoding="utf-8")) == {"channel-a": {"watermark": "old"}}


def test_save_leaves_no_tmp_file(tmp_path: Path):
    path = tmp_path / "state.json"

    state.save(path, {"channel-a": {"watermark": "old"}})

    assert not path.with_suffix(path.suffix + ".tmp").exists()
