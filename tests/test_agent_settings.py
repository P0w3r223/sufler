"""Testy AgentSettings (Faza 2 / ADR 0008) — bramka klucza, granice, źródło klucza.

``validate`` to granica startu runtime'u (klucz + sensowne limity), a ``from_env``
ma poprawnie wybierać źródło klucza. Oba czyste — testujemy bez SDK i bez sieci.

Środowisko czyści globalny fixture z ``tests/conftest.py`` (zdejmuje wszystkie ``SUFLER_*``
i klucze SDK), więc ręczna lista zmiennych do wyczyszczenia jest tu zbędna — test ustawia
tylko to, co faktycznie bada.
"""

from __future__ import annotations

import dataclasses

import pytest

from sufler.config import AgentSettings


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


def test_validate_rejects_unknown_thinking_type():
    with pytest.raises(ValueError, match="THINKING"):
        AgentSettings(api_key="k", thinking_type="on").validate()


def test_validate_accepts_disabled_thinking():
    AgentSettings(api_key="k", thinking_type="disabled").validate()  # nie rzuca


def test_from_env_reads_thinking_type(monkeypatch):
    monkeypatch.setenv("SUFLER_AGENT_THINKING", "disabled")

    assert AgentSettings.from_env().thinking_type == "disabled"


def test_from_env_prefers_workmate_key_then_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")

    assert AgentSettings.from_env().api_key == "a-key"

    monkeypatch.setenv("SUFLER_AGENT_API_KEY", "w-key")
    assert AgentSettings.from_env().api_key == "w-key"  # WORKMATE ma priorytet


def test_from_env_defaults_to_sonnet_model():
    settings = AgentSettings.from_env()

    assert settings.model == "claude-sonnet-5"
    assert (settings.max_tokens, settings.max_tool_iterations) == (128000, 8)
    assert settings.thinking_type == "adaptive"


def test_from_env_defaults_match_field_defaults():
    """Domyślne z ``from_env`` i z pól dataclass MUSZĄ być te same (ADR 0058).

    Wartości są zapisane w dwóch miejscach, więc rozjeżdżają się po cichu — a przy
    ``context_editing_keep_tool_uses`` rozjazd w dół oznacza czyszczenie wyników z bieżącej
    tury. Porównujemy wszystkie pola poza kluczem (ten pochodzi ze środowiska z definicji).
    """
    from_env = AgentSettings.from_env()
    defaults = AgentSettings()

    assert dataclasses.replace(from_env, api_key="") == defaults


def test_validate_rejects_keep_tool_uses_below_iteration_limit():
    """Bramka spójności: czyszczenie nie może sięgnąć wyników z bieżącej pętli narzędzi."""
    with pytest.raises(ValueError, match="KEEP_TOOL_USES"):
        AgentSettings(
            api_key="k", max_tool_iterations=8, context_editing_keep_tool_uses=3
        ).validate()


def test_validate_accepts_keep_tool_uses_equal_to_iteration_limit():
    AgentSettings(
        api_key="k", max_tool_iterations=4, context_editing_keep_tool_uses=4
    ).validate()  # nie rzuca


def test_api_key_absent_from_repr():
    # Sekret nie może wyciec do logu/traceback przez repr obiektu ustawień.
    assert "sekret-123" not in repr(AgentSettings(api_key="sekret-123"))
