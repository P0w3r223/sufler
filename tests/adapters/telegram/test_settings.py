"""Testy TelegramSettings — bramka tokenu, źródło z env, sekret w repr (Faza 2)."""

from __future__ import annotations

import pytest

from workmate.config import TelegramSettings


def test_validate_rejects_missing_token():
    with pytest.raises(ValueError, match="WORKMATE_TELEGRAM_BOT_TOKEN"):
        TelegramSettings(bot_token="").validate()


def test_validate_accepts_token():
    TelegramSettings(bot_token="123456:ABC-DEF").validate()  # nie rzuca


def test_from_env_reads_token(monkeypatch):
    monkeypatch.setenv("WORKMATE_TELEGRAM_BOT_TOKEN", "123456:ABC-DEF")

    assert TelegramSettings.from_env().bot_token == "123456:ABC-DEF"


def test_from_env_is_empty_without_var(monkeypatch):
    monkeypatch.delenv("WORKMATE_TELEGRAM_BOT_TOKEN", raising=False)

    assert TelegramSettings.from_env().bot_token == ""


def test_token_absent_from_repr():
    # Sekret nie może wyciec do logu/traceback przez repr obiektu ustawień.
    assert "123456:SECRET" not in repr(TelegramSettings(bot_token="123456:SECRET"))
