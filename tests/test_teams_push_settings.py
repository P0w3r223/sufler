"""Testy konfiguracji proaktywnego push do Teams (TeamsPushSettings, ADR 0022).

Środowisko czyści globalny fixture z ``tests/conftest.py`` (zdejmuje wszystkie ``SUFLER_*``),
więc ręczna lista zmiennych do wyczyszczenia — i ryzyko, że ktoś zapomni jej uzupełnić przy
nowym polu — są tu zbędne. Test ustawia tylko to, co faktycznie bada.
"""

from __future__ import annotations

import pytest

from sufler.config import TeamsPushSettings


def test_defaults_disabled():
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
        TeamsPushSettings(enable_chat=True, client_id="c", tenant_id="t").validate()


def test_validate_channel_requires_team_and_channel():
    with pytest.raises(ValueError, match="TEAM_ID|CHANNEL_ID"):
        TeamsPushSettings(enable_channel=True, client_id="c", tenant_id="t").validate()


def test_validate_passes_with_complete_channel_target():
    TeamsPushSettings(
        enable_channel=True,
        client_id="c",
        tenant_id="t",
        team_id="team-1",
        channel_id="chan-1",
    ).validate()  # nie rzuca


def test_authority_uses_tenant():
    assert TeamsPushSettings(tenant_id="abc").authority.endswith("/abc")


# --- ADR 0024 Faza 3a: wątkowanie kanału --------------------------------------


def test_channel_threading_defaults_false():
    assert TeamsPushSettings().enable_channel_threading is False
    assert TeamsPushSettings.from_env().enable_channel_threading is False


def test_from_env_reads_channel_threading(monkeypatch):
    monkeypatch.setenv("SUFLER_TEAMS_PUSH_ENABLE_CHANNEL_THREADING", "true")
    assert TeamsPushSettings.from_env().enable_channel_threading is True


def test_validate_rejects_threading_without_channel():
    # Wątki są tylko na kanale — włączone przy wyłączonym celu kanału to cicha sprzeczność.
    with pytest.raises(ValueError, match="CHANNEL_THREADING"):
        TeamsPushSettings(
            enable_chat=True,
            chat_user_id="u",
            client_id="c",
            tenant_id="t",
            enable_channel_threading=True,
        ).validate()


def test_validate_accepts_threading_with_channel():
    TeamsPushSettings(
        enable_channel=True,
        client_id="c",
        tenant_id="t",
        team_id="team-1",
        channel_id="chan-1",
        enable_channel_threading=True,
    ).validate()  # nie rzuca
