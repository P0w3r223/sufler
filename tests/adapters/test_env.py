"""Testy WSPÓLNEGO loadera ``.env`` drzwi (``adapters/inbound/env.py``).

Loader jest wygodą deva i granicą wczytania sekretu z ``.env`` — musi być odporny
na kodowanie (PowerShell zapisuje UTF-16 LE z BOM) i NIE nadpisywać realnego env.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from workmate.adapters.inbound.env import apply_env_file


def test_apply_env_file_parses_utf8_with_comments_and_quotes(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("WORKMATE_TEST_UTF8", raising=False)
    env = tmp_path / ".env"
    env.write_text('# komentarz\nWORKMATE_TEST_UTF8 = "abc123"\n', encoding="utf-8")

    apply_env_file(env)

    assert os.environ["WORKMATE_TEST_UTF8"] == "abc123"


def test_apply_env_file_handles_utf16_bom_from_powershell(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("WORKMATE_TEST_UTF16", raising=False)
    env = tmp_path / ".env"
    # PowerShell (Out-File/Set-Content) domyślnie zapisuje UTF-16 LE z BOM.
    env.write_text("WORKMATE_TEST_UTF16=xyz789\n", encoding="utf-16")

    apply_env_file(env)

    assert os.environ["WORKMATE_TEST_UTF16"] == "xyz789"


def test_apply_env_file_does_not_override_real_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WORKMATE_TEST_PRIO", "z-realnego-env")
    env = tmp_path / ".env"
    env.write_text("WORKMATE_TEST_PRIO=z-pliku\n", encoding="utf-8")

    apply_env_file(env)

    assert os.environ["WORKMATE_TEST_PRIO"] == "z-realnego-env"  # setdefault: env wygrywa


def test_apply_env_file_skips_blank_and_comment_lines(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("WORKMATE_TEST_KEEP", raising=False)
    env = tmp_path / ".env"
    env.write_text("\n# tylko komentarz\n\nWORKMATE_TEST_KEEP=1\n", encoding="utf-8")

    apply_env_file(env)

    assert os.environ["WORKMATE_TEST_KEEP"] == "1"


def test_apply_env_file_raises_clear_error_on_corrupt_encoding(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_bytes(b"\xff\xfe\x41")  # BOM UTF-16 + niepełny bajt → błąd dekodowania

    with pytest.raises(SystemExit, match="kodowanie"):
        apply_env_file(env)
