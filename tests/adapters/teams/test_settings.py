"""Testy TeamsSettings — bramka tożsamości i bezpieczne domyślne (Faza 2).

``validate`` to granica bezpieczeństwa drzwi Teams (lepiej nie wystartować niż
ruszyć bez auth), a ``from_env`` musi być fail-closed (anonimowy = świadomy opt-in).
Oba są czyste, więc testujemy je bez SDK i bez Azure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sufler.config import TeamsSettings

_TEAMS_VARS = (
    "SUFLER_TEAMS_APP_ID",
    "SUFLER_TEAMS_APP_PASSWORD",
    "SUFLER_TEAMS_TENANT_ID",
    "SUFLER_TEAMS_BIND_HOST",
    "SUFLER_TEAMS_PORT",
    "SUFLER_TEAMS_ANONYMOUS",
    "SUFLER_TEAMS_IDENTITIES",
    "SUFLER_TEAMS_ENABLE_NOTE_READ_AUTHZ",
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
    assert "SUFLER_TEAMS_APP_ID" in msg
    assert "SUFLER_TEAMS_APP_PASSWORD" in msg
    assert "SUFLER_TEAMS_TENANT_ID" in msg


def test_validate_names_only_the_missing_tenant_id():
    """Pułapka z researchu: single-tenant bez tenant_id → 401. Komunikat musi go wskazać."""
    with pytest.raises(ValueError) as exc:
        TeamsSettings(app_id="a", app_password="p").validate()

    msg = str(exc.value)
    assert "SUFLER_TEAMS_TENANT_ID" in msg
    assert "SUFLER_TEAMS_APP_ID" not in msg


# --- bramka odczytu bazy wiedzy (ADR 0062) ----------------------------------


def test_validate_rejects_read_authz_without_identity_map():
    """Bramka bez mapy tożsamości nie ma po czym rozpoznać nadawcy — fail-fast na starcie."""
    with pytest.raises(ValueError) as exc:
        TeamsSettings(
            anonymous_auth=True, bind_host="localhost", enable_note_read_authz=True
        ).validate()

    assert "SUFLER_TEAMS_IDENTITIES" in str(exc.value)


def test_validate_rejects_read_authz_also_in_authenticated_mode():
    """Sprawdzenie stoi PRZED gałęzią anonimową, więc obowiązuje w obu trybach transportu."""
    with pytest.raises(ValueError, match="SUFLER_TEAMS_IDENTITIES"):
        TeamsSettings(
            app_id="a", app_password="p", tenant_id="t", enable_note_read_authz=True
        ).validate()


def test_validate_passes_with_read_authz_and_existing_map(tmp_path: Path):
    identities = tmp_path / "identities.yaml"
    identities.write_text(
        "EMP-1:\n  aad_user_id: aad-anna\n  jira_user: anna@example.org\n", "utf-8"
    )

    TeamsSettings(
        anonymous_auth=True,
        bind_host="localhost",
        enable_note_read_authz=True,
        identities=identities,
    ).validate()  # nie rzuca


# --- from_env ---------------------------------------------------------------


def test_from_env_is_fail_closed_by_default(monkeypatch):
    for var in _TEAMS_VARS:
        monkeypatch.delenv(var, raising=False)

    settings = TeamsSettings.from_env()

    assert settings.anonymous_auth is False  # secure-by-default
    assert (settings.app_id, settings.app_password, settings.tenant_id) == ("", "", "")
    assert (settings.bind_host, settings.bind_port) == ("localhost", 3978)
    # Bramka odczytu (ADR 0062) domyślnie OFF — jak bliźniacza na drzwiach delegowanych.
    assert settings.enable_note_read_authz is False
    assert settings.identities == Path()


def test_from_env_applies_overrides(monkeypatch):
    monkeypatch.setenv("SUFLER_TEAMS_APP_ID", "app-123")
    monkeypatch.setenv("SUFLER_TEAMS_TENANT_ID", "tenant-9")
    monkeypatch.setenv("SUFLER_TEAMS_PORT", "4000")
    monkeypatch.setenv("SUFLER_TEAMS_ANONYMOUS", "true")
    monkeypatch.setenv("SUFLER_TEAMS_IDENTITIES", "/etc/sufler/identities.yaml")
    monkeypatch.setenv("SUFLER_TEAMS_ENABLE_NOTE_READ_AUTHZ", "true")

    settings = TeamsSettings.from_env()

    assert settings.app_id == "app-123"
    assert settings.tenant_id == "tenant-9"
    assert settings.bind_port == 4000
    assert settings.anonymous_auth is True
    assert settings.identities == Path("/etc/sufler/identities.yaml")
    assert settings.enable_note_read_authz is True


def test_from_env_then_validate_accepts_anonymous(monkeypatch):
    """Ścieżka startowa app.py::main: sam ANONYMOUS=true daje config, który przechodzi."""
    for var in _TEAMS_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SUFLER_TEAMS_ANONYMOUS", "true")

    TeamsSettings.from_env().validate()  # nie rzuca
