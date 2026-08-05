"""Testy ustawień pamięci rozmów i kompaktowania (``ConversationSettings``, ADR 0010/0014, 0058).

``validate`` to granica startu (sensowne limity), ``from_env`` czyta zmienne środowiskowe,
a ``compaction_threshold_tokens`` jest progiem BEZWZGLĘDNYM — nie ułamkiem okna modelu.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workmate.config import ConversationSettings

_DB = Path("x.db")


def test_threshold_is_absolute_and_independent_of_window_size(monkeypatch):
    """Próg nie skaluje się z oknem modelu (ADR 0058).

    Wcześniej liczyliśmy go jako ułamek ``WORKMATE_CONTEXT_WINDOW_TOKENS``, więc podbicie
    okna po cichu przesuwało próg w górę — model dłużej pracował w kontekście, z którego
    gorzej sięga po fakty. Zmienna okna nie ma już wpływu na próg; ten test to przypina.
    """
    monkeypatch.setenv("WORKMATE_CONTEXT_WINDOW_TOKENS", "1000000")
    monkeypatch.delenv("WORKMATE_COMPACTION_THRESHOLD_TOKENS", raising=False)

    assert ConversationSettings.from_env().compaction_threshold_tokens == 150_000


def test_validate_accepts_defaults():
    ConversationSettings(db_path=_DB).validate()  # nie rzuca


def test_validate_rejects_nonpositive_keep_turns():
    with pytest.raises(ValueError):
        ConversationSettings(db_path=_DB, compaction_keep_turns=0).validate()


def test_validate_rejects_nonpositive_threshold():
    with pytest.raises(ValueError):
        ConversationSettings(db_path=_DB, compaction_threshold_tokens=0).validate()


def test_from_env_reads_compaction_settings(monkeypatch):
    monkeypatch.setenv("WORKMATE_COMPACTION_ENABLED", "false")
    monkeypatch.setenv("WORKMATE_COMPACTION_THRESHOLD_TOKENS", "100000")
    monkeypatch.setenv("WORKMATE_COMPACTION_KEEP_TURNS", "6")
    monkeypatch.setenv("WORKMATE_COMPACTION_MODEL", "claude-haiku-4-5")

    s = ConversationSettings.from_env()

    assert s.compaction_enabled is False
    assert s.compaction_threshold_tokens == 100_000
    assert s.compaction_keep_turns == 6
    assert s.compaction_model == "claude-haiku-4-5"


def test_from_env_defaults_enable_compaction(monkeypatch):
    for name in (
        "WORKMATE_COMPACTION_ENABLED",
        "WORKMATE_COMPACTION_THRESHOLD_TOKENS",
        "WORKMATE_COMPACTION_KEEP_TURNS",
        "WORKMATE_COMPACTION_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    s = ConversationSettings.from_env()

    assert s.compaction_enabled is True
    assert s.compaction_threshold_tokens == 150_000
    assert s.compaction_keep_turns == 4
    assert s.compaction_model == ""  # pusty → model agenta w wiringu
