"""Testy TeamsSettings — bramka tożsamości i bezpieczne domyślne (Faza 2).

``validate`` to granica bezpieczeństwa drzwi Teams (lepiej nie wystartować niż
ruszyć bez auth), a ``from_env`` musi być fail-closed (anonimowy = świadomy opt-in).
Oba są czyste, więc testujemy je bez SDK i bez Azure.
"""

from __future__ import annotations

import pytest

from workmate.config import TeamsSettings

_TEAMS_VARS = (
    "WORKMATE_TEAMS_APP_ID",
    "WORKMATE_TEAMS_APP_PASSWORD",
    "WORKMATE_TEAMS_TENANT_ID",
    "WORKMATE_TEAMS_BIND_HOST",
    "WORKMATE_TEAMS_PORT",
    "WORKMATE_TEAMS_ANONYMOUS",
)


# --- validate ---------------------------------------------------------------


def test_validate_passes_in_anonymous_mode_on_loopback():
    TeamsSettings(anonymous_auth=True, bind_host="localhost").validate()  # nie rzuca


def test_validate_rejects_anonymous_mode_on_non_loopback_host():
    with pytest.raises(ValueError, match="loopback"):
        TeamsSettings(anonymous_auth=True, bind_host="0.0.0.0").validate()


def test_validate_passes_with_full_single_tenant_identity():
    TeamsSettings(app_id="a", app_password="p", tenant_id="t").validate()  # nie rzuca


def test_validate_rejects_authenticated_mode_without_identity():
    with pytest.raises(ValueError) as exc:
        TeamsSettings().validate()

    msg = str(exc.value)
    assert "WORKMATE_TEAMS_APP_ID" in msg
    assert "WORKMATE_TEAMS_APP_PASSWORD" in msg
    assert "WORKMATE_TEAMS_TENANT_ID" in msg


def test_validate_names_only_the_missing_tenant_id():
    """Pułapka z researchu: single-tenant bez tenant_id → 401. Komunikat musi go wskazać."""
    with pytest.raises(ValueError) as exc:
        TeamsSettings(app_id="a", app_password="p").validate()

    msg = str(exc.value)
    assert "WORKMATE_TEAMS_TENANT_ID" in msg
    assert "WORKMATE_TEAMS_APP_ID" not in msg


# --- from_env ---------------------------------------------------------------


def test_from_env_is_fail_closed_by_default(monkeypatch):
    for var in _TEAMS_VARS:
        monkeypatch.delenv(var, raising=False)

    settings = TeamsSettings.from_env()

    assert settings.anonymous_auth is False  # secure-by-default
    assert (settings.app_id, settings.app_password, settings.tenant_id) == ("", "", "")
    assert (settings.bind_host, settings.bind_port) == ("localhost", 3978)


def test_from_env_applies_overrides(monkeypatch):
    monkeypatch.setenv("WORKMATE_TEAMS_APP_ID", "app-123")
    monkeypatch.setenv("WORKMATE_TEAMS_TENANT_ID", "tenant-9")
    monkeypatch.setenv("WORKMATE_TEAMS_PORT", "4000")
    monkeypatch.setenv("WORKMATE_TEAMS_ANONYMOUS", "true")

    settings = TeamsSettings.from_env()

    assert settings.app_id == "app-123"
    assert settings.tenant_id == "tenant-9"
    assert settings.bind_port == 4000
    assert settings.anonymous_auth is True


def test_from_env_then_validate_accepts_anonymous(monkeypatch):
    """Ścieżka startowa app.py::main: sam ANONYMOUS=true daje config, który przechodzi."""
    for var in _TEAMS_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WORKMATE_TEAMS_ANONYMOUS", "true")

    TeamsSettings.from_env().validate()  # nie rzuca
