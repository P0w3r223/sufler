"""Wczytywanie ``.env`` — jedyny moduł pakietu, który MUTUJE ``os.environ``.

Środowisko podmieniamy w całości (``os.environ`` na zwykły słownik), więc testy nie zostawiają
po sobie zmiennych w procesie pytest.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from claude_summary.adapters.env import apply_env_file, load_dotenv


@pytest.fixture
def fake_environ(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    environ: dict[str, str] = {}
    monkeypatch.setattr(os, "environ", environ)
    return environ


def test_reads_utf8_and_strips_quotes(fake_environ: dict[str, str], tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# komentarz",
                "",
                'CLAUDE_SUMMARY_AUTHOR="jan@firma.pl"',
                "CLAUDE_SUMMARY_TZ='Europe/Warsaw'",
                "CLAUDE_SUMMARY_MODEL=claude-sonnet-5",
                "linia bez znaku równości",
            ]
        ),
        encoding="utf-8",
    )
    apply_env_file(env_file)
    loaded = {k: v for k, v in fake_environ.items() if k.startswith("CLAUDE_SUMMARY_")}
    assert loaded == {
        "CLAUDE_SUMMARY_AUTHOR": "jan@firma.pl",
        "CLAUDE_SUMMARY_TZ": "Europe/Warsaw",
        "CLAUDE_SUMMARY_MODEL": "claude-sonnet-5",
    }


def test_real_environment_wins_over_file(fake_environ: dict[str, str], tmp_path: Path) -> None:
    fake_environ["CLAUDE_SUMMARY_TZ"] = "UTC"
    env_file = tmp_path / ".env"
    env_file.write_text("CLAUDE_SUMMARY_TZ=Europe/Warsaw\n", encoding="utf-8")
    apply_env_file(env_file)
    assert fake_environ["CLAUDE_SUMMARY_TZ"] == "UTC"  # setdefault, nie nadpisanie


def test_reads_utf16_with_bom_from_powershell(fake_environ: dict[str, str], tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_bytes("CLAUDE_SUMMARY_MODEL=claude-opus\n".encode("utf-16"))
    apply_env_file(env_file)
    assert fake_environ["CLAUDE_SUMMARY_MODEL"] == "claude-opus"


def test_undecodable_file_exits_readably(fake_environ: dict[str, str], tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"KLUCZ=\xff\xfe\xfa\x00warto\x9c\xe6")
    with pytest.raises(SystemExit, match="UTF-8"):
        apply_env_file(env_file)


def test_load_dotenv_finds_repo_root_and_tolerates_missing_file(
    fake_environ: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Korzeń szukamy od pliku modułu w górę — udajemy pakiet leżący pod ``tmp_path``.
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    monkeypatch.setattr(
        "claude_summary.adapters.env.Path.resolve", lambda self: tmp_path / "pkg" / "env.py"
    )

    load_dotenv()  # brak .env nie jest błędem
    assert "CLAUDE_SUMMARY_CONSENT" not in fake_environ

    (tmp_path / ".env").write_text("CLAUDE_SUMMARY_CONSENT=1\n", encoding="utf-8")
    load_dotenv()
    assert fake_environ["CLAUDE_SUMMARY_CONSENT"] == "1"
