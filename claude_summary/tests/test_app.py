"""Integracja CLI: bramka zgody (fail-closed) i pełny bieg bez repo (same prompty)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from claude_summary.app import _Args, run
from claude_summary.config import Settings


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "projects_dir": tmp_path / "projects",
        "output_dir": tmp_path / "out",
        "tz_name": "Europe/Warsaw",
        "default_days": 7,
        "author": "",
        "enable_llm": False,
        "model": "m",
        "max_tokens": 10,
        "consent": False,
        "api_key": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _write_prompt(projects: Path, folder: str, content: str, cwd: str, timestamp: str) -> None:
    path = projects / folder / "s.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "type": "user",
        "promptSource": "typed",
        "origin": {"kind": "human"},
        "isSidechain": False,
        "sessionId": "s",
        "cwd": cwd,
        "timestamp": timestamp,
        "message": {"role": "user", "content": content},
    }
    path.write_text(json.dumps(line, ensure_ascii=False), encoding="utf-8")


def test_consent_gate_blocks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run(_Args(), _settings(tmp_path))
    assert code == 1
    assert "--consent" in capsys.readouterr().err


def test_pipeline_without_repo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    projects = tmp_path / "projects"
    _write_prompt(projects, "C--proj", "zrobiłem X", "C:\\x", "2026-07-20T09:00:00.000Z")
    settings = _settings(tmp_path, consent=True)
    args = _Args(since=date(2026, 7, 20), until=date(2026, 7, 20))
    code = run(args, settings)
    assert code == 0
    out = capsys.readouterr().out
    assert "2026-07-20" in out
    assert "zrobiłem X" in out
