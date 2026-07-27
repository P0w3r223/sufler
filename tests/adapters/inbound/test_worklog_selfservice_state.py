"""Stan idempotencji drzwi self-service (ADR 0038): roundtrip, tolerancja, cap, klucz."""

from __future__ import annotations

from pathlib import Path

from workmate.adapters.inbound.worklog_selfservice import state as state_store


def test_missing_file_is_empty(tmp_path: Path) -> None:
    assert state_store.load(tmp_path / "nie-ma.json") == {}


def test_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    state_store.save(path, {"EMP-1:2026-W29": "2026-07-24T10:00:00+02:00"})
    assert state_store.load(path) == {"EMP-1:2026-W29": "2026-07-24T10:00:00+02:00"}


def test_corrupt_file_degrades_to_empty(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text("{nie json", encoding="utf-8")
    assert state_store.load(path) == {}


def test_non_dict_degrades_to_empty(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert state_store.load(path) == {}


def test_submission_key_format() -> None:
    assert state_store.submission_key("EMP-1", "2026-W29") == "EMP-1:2026-W29"


def test_save_caps_to_newest_entries(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    # 600 wpisów; znacznik rośnie z indeksem, więc najnowsze mają największe wartości.
    big = {f"EMP-{i}:2026-W29": f"2026-07-24T{i:04d}" for i in range(600)}
    state_store.save(path, big)
    loaded = state_store.load(path)
    assert len(loaded) == 500  # przycięte do _CAP
    assert "EMP-599:2026-W29" in loaded  # najnowszy został
    assert "EMP-0:2026-W29" not in loaded  # najstarszy wypadł
