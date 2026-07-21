"""Testy drzwi kart czasu (ADR 0035) — stan idempotencji, blokada instancji, konfiguracja.

Bez sieci: sprawdzamy to, co decyduje o tym, czy ktoś dostanie wiadomość DWA razy albo wcale.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workmate.adapters.inbound.single_instance import (
    AlreadyRunningError,
    acquire_single_instance_lock,
)
from workmate.adapters.inbound.worklogi import state as state_store
from workmate.config import WorklogiSettings

# --- stan idempotencji ------------------------------------------------------------


def test_roundtrip_preserves_entries(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state_store.save(path, {"2026-W29:EMP-042": "2026-07-17T16:00:00+02:00"})
    assert state_store.load(path) == {"2026-W29:EMP-042": "2026-07-17T16:00:00+02:00"}


def test_missing_state_starts_empty(tmp_path: Path) -> None:
    assert state_store.load(tmp_path / "nie-ma.json") == {}


def test_corrupt_state_starts_empty_instead_of_crashing(tmp_path: Path) -> None:
    """Martwy proces nie wyśle nic NIKOMU przez tydzień — gorsze niż nadmiarowa wiadomość."""
    path = tmp_path / "state.json"
    path.write_text("{nie json", encoding="utf-8")
    assert state_store.load(path) == {}


def test_state_of_wrong_shape_starts_empty(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("[1,2,3]", encoding="utf-8")
    assert state_store.load(path) == {}


def test_save_is_atomic_leaving_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state_store.save(path, {"a": "b"})
    assert list(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_save_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "glebiej" / "state.json"
    state_store.save(path, {"a": "b"})
    assert path.exists()


def test_key_is_scoped_per_week_and_person() -> None:
    assert state_store.key("2026-W29", "EMP-042") == "2026-W29:EMP-042"
    assert state_store.key("2026-W30", "EMP-042") != state_store.key("2026-W29", "EMP-042")


def test_prune_keeps_only_requested_weeks() -> None:
    state = {
        "2026-W29:EMP-042": "x",
        "2026-W28:EMP-042": "x",
        "2026-W10:EMP-017": "x",
    }
    pruned = state_store.prune(state, keep_weeks=("2026-W29", "2026-W28"))
    assert set(pruned) == {"2026-W29:EMP-042", "2026-W28:EMP-042"}


# --- blokada jednej instancji -----------------------------------------------------


def test_second_instance_is_refused(tmp_path: Path) -> None:
    """Dwa procesy obeszłyby idempotencję i wysłały ludziom po dwie wiadomości."""
    path = tmp_path / "state.json"
    with acquire_single_instance_lock(path), pytest.raises(AlreadyRunningError, match="Inna"):
        acquire_single_instance_lock(path)


def test_lock_is_released_after_the_context_exits(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    with acquire_single_instance_lock(path):
        pass
    with acquire_single_instance_lock(path):  # ponowne zajęcie musi się udać
        pass


def test_lock_file_records_the_pid(tmp_path: Path) -> None:
    """PID w pliku pozwala operatorowi znaleźć proces trzymający blokadę.

    Czytamy PO zwolnieniu: na Windows ``msvcrt`` blokuje bajt 0 wyłącznie, więc odczyt
    w trakcie trzymania blokady kończy się ``PermissionError``.
    """
    import os

    path = tmp_path / "state.json"
    with acquire_single_instance_lock(path):
        pass
    assert (tmp_path / "state.json.lock").read_text(encoding="utf-8") == str(os.getpid())


# --- konfiguracja -----------------------------------------------------------------


def _settings(**kw) -> WorklogiSettings:
    base: dict = {
        "enabled": True,
        "output_dir": Path("D:/worklogi"),
        "team_id": "team-1",
        "hours_path": Path("hours.json"),
    }
    base.update(kw)
    return WorklogiSettings(**base)


def test_disabled_settings_pass_validation(tmp_path: Path) -> None:
    WorklogiSettings().validate(data_dir=tmp_path / "data")


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("output_dir", "OUTPUT_DIR"),
        ("identities_path", "IDENTITIES"),
        ("team_id", "TEAM_ID"),
    ],
)
def test_enabled_requires_targets(field: str, expected: str, tmp_path: Path) -> None:
    empty = Path() if field != "team_id" else ""
    with pytest.raises(ValueError, match=expected):
        _settings(**{field: empty}).validate(data_dir=tmp_path / "data")


def test_missing_identity_file_is_a_hard_error(tmp_path: Path) -> None:
    """Bez mapy tożsamości NIKT nie dostanie arkusza — to musi paść przy starcie."""
    with pytest.raises(ValueError, match="mapa tożsamości nie istnieje"):
        _settings(identities_path=tmp_path / "nie-ma.yaml").validate(data_dir=tmp_path / "data")


def test_output_dir_inside_data_is_rejected(tmp_path: Path) -> None:
    """Arkusze z godzinami ludzi nie mogą trafić do bazy wiedzy, którą agent czyta."""
    data = tmp_path / "data"
    identities = tmp_path / "id.yaml"
    identities.write_text("EMP-1:\n  aad_user_id: a\n  jira_user: b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="nie może leżeć wewnątrz katalogu danych"):
        _settings(output_dir=data / "arkusze", identities_path=identities).validate(data_dir=data)


def test_unknown_timezone_is_rejected_even_when_disabled(tmp_path: Path) -> None:
    """Literówka w strefie nie może spać do dnia, w którym ktoś przestawi bramkę."""
    with pytest.raises(ValueError, match="strefą czasową"):
        WorklogiSettings(tz_name="Nie/Ma").validate(data_dir=tmp_path / "data")


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("run_weekday", 9, "RUN_WEEKDAY"),
        ("run_hour", 25, "RUN_HOUR"),
        ("run_minute", 61, "RUN_MINUTE"),
        ("start_hour", -1, "START_HOUR"),
        ("max_hours_per_day", 0, "MAX_HOURS_PER_DAY"),
        ("max_hours_per_day", 99.0, "MAX_HOURS_PER_DAY"),
        ("max_catchup_days", 99, "MAX_CATCHUP_DAYS"),
    ],
)
def test_out_of_range_values_are_rejected(field, value, expected, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=expected):
        WorklogiSettings(**{field: value}).validate(data_dir=tmp_path / "data")


def test_defaults_are_conservative() -> None:
    """Bramka OFF, tryb próbny ON, piątek 16:00 — nic nie wychodzi bez świadomej decyzji."""
    settings = WorklogiSettings()
    assert settings.enabled is False
    assert settings.dry_run is True
    assert (settings.run_weekday, settings.run_hour) == (4, 16)


def test_from_env_reads_the_surface(monkeypatch) -> None:
    monkeypatch.setenv("WORKMATE_WORKLOGI_ENABLED", "true")
    monkeypatch.setenv("WORKMATE_WORKLOGI_DRY_RUN", "false")
    monkeypatch.setenv("WORKMATE_WORKLOGI_TEAM_ID", " team-9 ")
    monkeypatch.setenv("WORKMATE_WORKLOGI_ONLY_SOURCE_IDS", "EMP-042, EMP-017")
    monkeypatch.setenv("WORKMATE_WORKLOGI_RUN_HOUR", "15")
    settings = WorklogiSettings.from_env()
    assert settings.enabled is True and settings.dry_run is False
    assert settings.team_id == "team-9"
    assert settings.only_source_ids == ("EMP-042", "EMP-017")
    assert settings.run_hour == 15


def test_teams_push_scopes_include_team_members() -> None:
    """Lista osób z Graph wymaga TeamMember.Read.All — ta sama aplikacja co Powiadomienia_teams."""
    from workmate.config import TeamsPushSettings

    assert "TeamMember.Read.All" in TeamsPushSettings().scopes
