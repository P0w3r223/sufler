"""Konfiguracja: wartości domyślne, walidacja strefy, bramka zgody z env."""

from __future__ import annotations

import pytest

from claude_summary.config import Settings

_ENV_VARS = (
    "CLAUDE_SUMMARY_PROJECTS_DIR",
    "CLAUDE_SUMMARY_OUTPUT_DIR",
    "CLAUDE_SUMMARY_TZ",
    "CLAUDE_SUMMARY_DEFAULT_DAYS",
    "CLAUDE_SUMMARY_AUTHOR",
    "CLAUDE_SUMMARY_LLM",
    "CLAUDE_SUMMARY_MODEL",
    "CLAUDE_SUMMARY_MAX_TOKENS",
    "CLAUDE_SUMMARY_CONSENT",
    "CLAUDE_SUMMARY_API_KEY",
    "ANTHROPIC_API_KEY",
)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    settings = Settings.from_env()
    settings.validate()
    assert settings.default_days == 7
    assert settings.tz_name == "Europe/Warsaw"
    assert settings.consent is False
    assert settings.enable_llm is False


def test_validate_rejects_bad_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_SUMMARY_TZ", "Nowhere/Nope")
    with pytest.raises(ValueError):
        Settings.from_env().validate()


def test_consent_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_SUMMARY_CONSENT", "1")
    assert Settings.from_env().consent is True
