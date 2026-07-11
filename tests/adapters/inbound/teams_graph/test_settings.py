"""Testy TeamsGraphSettings — bramka tożsamości aplikacji Entra i sensowność limitów.

``validate`` to granica startu drzwi delegowanych (ADR 0015): bez ``client_id``/
``tenant_id`` proces nie ma jak się zalogować (device-code), więc lepiej nie ruszyć niż
wystartować z placeholderem. Ustawienia są czyste, więc testujemy je bez MSAL i bez sieci.
"""
from __future__ import annotations

import pytest

from workmate.config import TeamsGraphSettings

_TEAMS_GRAPH_VARS = (
    "WORKMATE_TEAMS_GRAPH_CLIENT_ID",
    "WORKMATE_TEAMS_GRAPH_TENANT_ID",
    "WORKMATE_TEAMS_GRAPH_SCOPES",
    "WORKMATE_TEAMS_GRAPH_TOKEN_CACHE",
    "WORKMATE_TEAMS_GRAPH_STATE",
    "WORKMATE_TEAMS_GRAPH_WATCH",
    "WORKMATE_TEAMS_GRAPH_POLL_INTERVAL",
    "WORKMATE_TEAMS_GRAPH_TOP_ROOTS",
    "WORKMATE_TEAMS_GRAPH_TOP_REPLIES",
    "WORKMATE_TEAMS_GRAPH_ACTIVE_IDLE_HOURS",
)


def _valid(**overrides: object) -> TeamsGraphSettings:
    """Config przechodzący ``validate`` — testy nadpisują tylko badane pole."""
    base: dict[str, object] = {"client_id": "app-1", "tenant_id": "tenant-1"}
    base.update(overrides)
    return TeamsGraphSettings(**base)  # type: ignore[arg-type]


# --- validate: tożsamość aplikacji -----------------------------------------


def test_validate_passes_with_full_identity():
    _valid().validate()  # nie rzuca


def test_validate_rejects_missing_both_identity_fields():
    with pytest.raises(ValueError) as exc:
        TeamsGraphSettings().validate()

    msg = str(exc.value)
    assert "WORKMATE_TEAMS_GRAPH_CLIENT_ID" in msg
    assert "WORKMATE_TEAMS_GRAPH_TENANT_ID" in msg


def test_validate_names_only_the_missing_client_id():
    """Komunikat wskazuje DOKŁADNIE brakujące pole — nie myli o czym mowa."""
    with pytest.raises(ValueError) as exc:
        TeamsGraphSettings(tenant_id="tenant-1").validate()

    msg = str(exc.value)
    assert "WORKMATE_TEAMS_GRAPH_CLIENT_ID" in msg
    assert "WORKMATE_TEAMS_GRAPH_TENANT_ID" not in msg


def test_validate_names_only_the_missing_tenant_id():
    with pytest.raises(ValueError) as exc:
        TeamsGraphSettings(client_id="app-1").validate()

    msg = str(exc.value)
    assert "WORKMATE_TEAMS_GRAPH_TENANT_ID" in msg
    assert "WORKMATE_TEAMS_GRAPH_CLIENT_ID" not in msg


# --- validate: sensowność limitów ------------------------------------------


def test_validate_rejects_empty_scopes():
    with pytest.raises(ValueError, match="SCOPES"):
        _valid(scopes=()).validate()


@pytest.mark.parametrize(
    ("field", "var_fragment"),
    [
        ("poll_interval_s", "POLL_INTERVAL"),
        ("top_roots", "TOP_ROOTS"),
        ("top_replies", "TOP_REPLIES"),
        ("active_idle_hours", "ACTIVE_IDLE_HOURS"),
    ],
)
def test_validate_rejects_below_one_limits(field, var_fragment):
    """Każdy limit < 1 jest odrzucany — 0 kanałów/rund/godzin nie ma sensu."""
    with pytest.raises(ValueError, match=var_fragment):
        _valid(**{field: 0}).validate()


def test_validate_rejects_zero_active_idle_because_it_kills_multiturn():
    """0 h eksmitowałoby każdy wątek natychmiast po rundzie — koniec wielotury."""
    with pytest.raises(ValueError, match="ACTIVE_IDLE_HOURS"):
        _valid(active_idle_hours=0).validate()


# --- from_env ---------------------------------------------------------------


def test_from_env_defaults_when_unset(monkeypatch):
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)

    settings = TeamsGraphSettings.from_env()

    assert (settings.client_id, settings.tenant_id) == ("", "")
    assert settings.watch == ()  # brak WATCH → tryb odkrywania
    assert (settings.poll_interval_s, settings.top_roots, settings.top_replies) == (
        10,
        20,
        50,
    )
    assert settings.active_idle_hours == 24


def test_from_env_parses_watch_pairs(monkeypatch):
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(
        "WORKMATE_TEAMS_GRAPH_WATCH", "team-a:chan-1, team-b:chan-2"
    )

    settings = TeamsGraphSettings.from_env()

    assert settings.watch == (("team-a", "chan-1"), ("team-b", "chan-2"))


def test_from_env_skips_incomplete_watch_entries(monkeypatch):
    """Wpis bez ``:channel`` jest pomijany — nie da się z niego zbudować pary."""
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_WATCH", "team-a:chan-1,broken-entry")

    settings = TeamsGraphSettings.from_env()

    assert settings.watch == (("team-a", "chan-1"),)


def test_from_env_reads_identity_and_limits(monkeypatch):
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_CLIENT_ID", "app-xyz")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_TENANT_ID", "tenant-xyz")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_POLL_INTERVAL", "5")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_TOP_ROOTS", "3")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_TOP_REPLIES", "7")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_ACTIVE_IDLE_HOURS", "48")

    settings = TeamsGraphSettings.from_env()

    assert settings.client_id == "app-xyz"
    assert settings.tenant_id == "tenant-xyz"
    assert settings.poll_interval_s == 5
    assert settings.top_roots == 3
    assert settings.top_replies == 7
    assert settings.active_idle_hours == 48


def test_from_env_then_validate_accepts_full_identity(monkeypatch):
    """Ścieżka startowa app.py::main: env z tożsamością daje config, który przechodzi."""
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_CLIENT_ID", "app-1")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_TENANT_ID", "tenant-1")

    TeamsGraphSettings.from_env().validate()  # nie rzuca


def test_authority_url_is_single_tenant_from_tenant_id():
    settings = TeamsGraphSettings(client_id="app-1", tenant_id="tenant-1")

    assert settings.authority == "https://login.microsoftonline.com/tenant-1"
