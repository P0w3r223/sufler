from pathlib import Path

from powiadomienia_teams.state import (
    AWAITING_REPLY,
    PendingReminder,
    load_state,
    save_state,
)


def test_round_trip(tmp_path: Path):
    path = tmp_path / "state.json"
    state = {
        "u1": PendingReminder(
            member_id="u1",
            member_name="Ala",
            chat_id="chat-1",
            week_start="2026-07-20",
            status=AWAITING_REPLY,
            watermark="2026-07-19T18:00:00Z",
            resolved=[{"weekday": 0, "start": "08:00", "end": "16:00"}],
        )
    }
    save_state(path, state)
    loaded = load_state(path)
    assert loaded == state
    assert loaded["u1"].resolved[0]["start"] == "08:00"


def test_load_missing_returns_empty(tmp_path: Path):
    assert load_state(tmp_path / "nope.json") == {}


def test_load_ignores_unknown_fields(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text(
        '{"u1": {"member_id":"u1","member_name":"Ala","chat_id":"c",'
        '"week_start":"2026-07-20","status":"awaiting_reply","future_field":"x"}}',
        encoding="utf-8",
    )
    loaded = load_state(path)
    assert loaded["u1"].member_id == "u1"


def test_load_corrupt_file_returns_empty(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text("{ not valid json", encoding="utf-8")
    assert load_state(path) == {}
