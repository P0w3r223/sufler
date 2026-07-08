"""Testy AgentSettings (Faza 2 / ADR 0008) — bramka klucza, granice, źródło klucza.

``validate`` to granica startu runtime'u (klucz + sensowne limity), a ``from_env``
ma poprawnie wybierać źródło klucza. Oba czyste — testujemy bez SDK i bez sieci.
"""
from __future__ import annotations

import pytest

from workmate.config import AgentSettings

_AGENT_VARS = (
    "WORKMATE_AGENT_API_KEY",
    "ANTHROPIC_API_KEY",
    "WORKMATE_AGENT_MODEL",
    "WORKMATE_AGENT_MAX_TOKENS",
    "WORKMATE_AGENT_MAX_TOOL_ITERATIONS",
)


def test_validate_rejects_empty_key():
    with pytest.raises(ValueError, match="klucz"):
        AgentSettings(api_key="").validate()


def test_validate_rejects_nonpositive_iterations():
    with pytest.raises(ValueError, match="MAX_TOOL_ITERATIONS"):
        AgentSettings(api_key="k", max_tool_iterations=0).validate()


def test_validate_rejects_nonpositive_max_tokens():
    with pytest.raises(ValueError, match="MAX_TOKENS"):
        AgentSettings(api_key="k", max_tokens=0).validate()


def test_validate_accepts_sane_config():
    AgentSettings(api_key="k", max_tokens=4096, max_tool_iterations=8).validate()  # nie rzuca


def test_from_env_prefers_workmate_key_then_anthropic(monkeypatch):
    for var in _AGENT_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")

    assert AgentSettings.from_env().api_key == "a-key"

    monkeypatch.setenv("WORKMATE_AGENT_API_KEY", "w-key")
    assert AgentSettings.from_env().api_key == "w-key"  # WORKMATE ma priorytet


def test_from_env_defaults_to_haiku_model(monkeypatch):
    for var in _AGENT_VARS:
        monkeypatch.delenv(var, raising=False)

    settings = AgentSettings.from_env()

    assert settings.model == "claude-haiku-4-5"
    assert (settings.max_tokens, settings.max_tool_iterations) == (4096, 8)


def test_api_key_absent_from_repr():
    # Sekret nie może wyciec do logu/traceback przez repr obiektu ustawień.
    assert "sekret-123" not in repr(AgentSettings(api_key="sekret-123"))
