"""Testy konfiguracji proaktywnego push do Teams (TeamsPushSettings, ADR 0022)."""
from __future__ import annotations

import pytest

from workmate.config import TeamsPushSettings

_PUSH_VARS = (
    "WORKMATE_TEAMS_PUSH_CLIENT_ID",
    "WORKMATE_TEAMS_PUSH_TENANT_ID",
    "WORKMATE_TEAMS_PUSH_SCOPES",
    "WORKMATE_TEAMS_PUSH_TOKEN_CACHE",
    "WORKMATE_TEAMS_PUSH_CHAT_USER_ID",
    "WORKMATE_TEAMS_PUSH_TEAM_ID",
    "WORKMATE_TEAMS_PUSH_CHANNEL_ID",
    "WORKMATE_TEAMS_PUSH_ENABLE_CHAT",
    "WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL",
)


def _clear(monkeypatch):
    for var in _PUSH_VARS:
        monkeypatch.delenv(var, raising=False)


def test_defaults_disabled(monkeypatch):
    _clear(monkeypatch)
    settings = TeamsPushSettings.from_env()
    assert settings.enabled is False
    assert "Chat.Create" in settings.scopes
    assert "ChannelMessage.Send" in settings.scopes


def test_enabled_when_a_target_on():
    assert TeamsPushSettings(enable_chat=True).enabled is True
    assert TeamsPushSettings(enable_channel=True).enabled is True


def test_validate_noop_when_disabled():
    TeamsPushSettings().validate()  # żaden cel → brak wymagań (ingest-only)


def test_validate_requires_app_identity_when_enabled():
    with pytest.raises(ValueError, match="CLIENT_ID|TENANT_ID"):
        TeamsPushSettings(enable_chat=True, chat_user_id="u").validate()


def test_validate_chat_requires_user_id():
    with pytest.raises(ValueError, match="CHAT_USER_ID"):
        TeamsPushSettings(
            enable_chat=True, client_id="c", tenant_id="t"
        ).validate()


def test_validate_channel_requires_team_and_channel():
    with pytest.raises(ValueError, match="TEAM_ID|CHANNEL_ID"):
        TeamsPushSettings(
            enable_channel=True, client_id="c", tenant_id="t"
        ).validate()


def test_validate_passes_with_complete_channel_target():
    TeamsPushSettings(
        enable_channel=True, client_id="c", tenant_id="t",
        team_id="team-1", channel_id="chan-1",
    ).validate()  # nie rzuca


def test_authority_uses_tenant():
    assert TeamsPushSettings(tenant_id="abc").authority.endswith("/abc")
