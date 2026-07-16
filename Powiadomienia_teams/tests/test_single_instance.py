from pathlib import Path

import pytest

from powiadomienia_teams.single_instance import (
    AlreadyRunningError,
    acquire_single_instance_lock,
)


def test_second_acquire_is_blocked(tmp_path: Path):
    state = tmp_path / "state.json"
    first = acquire_single_instance_lock(state)
    try:
        with pytest.raises(AlreadyRunningError):
            acquire_single_instance_lock(state)  # druga instancja odmawia startu
    finally:
        first.close()


def test_lock_released_after_close(tmp_path: Path):
    state = tmp_path / "state.json"
    acquire_single_instance_lock(state).close()  # zajmij i zwolnij
    acquire_single_instance_lock(state).close()  # ponowne zajęcie po zwolnieniu OK


def test_lock_file_created_next_to_state(tmp_path: Path):
    state = tmp_path / "state.json"
    handle = acquire_single_instance_lock(state)
    try:
        assert (tmp_path / "state.json.lock").exists()
    finally:
        handle.close()
