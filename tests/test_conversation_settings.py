"""Testy ustawień pamięci rozmów i kompaktowania (``ConversationSettings``, ADR 0010/0014).

``validate`` to granica startu (sensowne limity), ``from_env`` czyta zmienne środowiskowe,
a ``compaction_threshold_tokens`` liczy próg triggera z okna kontekstu i ułamka.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workmate.config import ConversationSettings

_DB = Path("x.db")


def test_threshold_tokens_is_fraction_of_window():
    s = ConversationSettings(
        db_path=_DB, context_window_tokens=1_000_000, compaction_threshold_fraction=0.70
    )
    assert s.compaction_threshold_tokens() == 700_000


def test_validate_accepts_defaults():
    ConversationSettings(db_path=_DB).validate()  # nie rzuca


def test_validate_rejects_fraction_out_of_range():
    with pytest.raises(ValueError):
        ConversationSettings(db_path=_DB, compaction_threshold_fraction=0.0).validate()
    with pytest.raises(ValueError):
        ConversationSettings(db_path=_DB, compaction_threshold_fraction=1.5).validate()


def test_validate_rejects_nonpositive_keep_turns():
    with pytest.raises(ValueError):
        ConversationSettings(db_path=_DB, compaction_keep_turns=0).validate()


def test_validate_rejects_nonpositive_window():
    with pytest.raises(ValueError):
        ConversationSettings(db_path=_DB, context_window_tokens=0).validate()


def test_from_env_reads_compaction_settings(monkeypatch):
    monkeypatch.setenv("WORKMATE_COMPACTION_ENABLED", "false")
    monkeypatch.setenv("WORKMATE_CONTEXT_WINDOW_TOKENS", "200000")
    monkeypatch.setenv("WORKMATE_COMPACTION_THRESHOLD_FRACTION", "0.5")
    monkeypatch.setenv("WORKMATE_COMPACTION_KEEP_TURNS", "6")
    monkeypatch.setenv("WORKMATE_COMPACTION_MODEL", "claude-haiku-4-5")

    s = ConversationSettings.from_env()

    assert s.compaction_enabled is False
    assert s.context_window_tokens == 200_000
    assert s.compaction_threshold_fraction == 0.5
    assert s.compaction_keep_turns == 6
    assert s.compaction_model == "claude-haiku-4-5"
    assert s.compaction_threshold_tokens() == 100_000


def test_from_env_defaults_enable_compaction(monkeypatch):
    for name in (
        "WORKMATE_COMPACTION_ENABLED",
        "WORKMATE_CONTEXT_WINDOW_TOKENS",
        "WORKMATE_COMPACTION_THRESHOLD_FRACTION",
        "WORKMATE_COMPACTION_KEEP_TURNS",
        "WORKMATE_COMPACTION_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    s = ConversationSettings.from_env()

    assert s.compaction_enabled is True
    assert s.compaction_keep_turns == 4
    assert s.compaction_model == ""  # pusty → model agenta w wiringu
