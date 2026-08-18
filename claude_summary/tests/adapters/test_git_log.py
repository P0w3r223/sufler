"""Parsowanie wyjścia ``git log`` — czysto, bez uruchamiania gita — oraz ścieżki błędu procesu."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from claude_summary.adapters import git_log
from claude_summary.adapters.git_log import parse_git_log, resolve_author, run_git_log

SEP = "\x1f"


def _line(sha: str, timestamp: str, author: str, subject: str) -> str:
    return SEP.join([sha, timestamp, author, subject])


def test_parse_git_log_basic() -> None:
    output = "\n".join(
        [
            _line("abc123", "2026-07-17T10:03:11+02:00", "Jan Kowalski", "feat: x, y"),
            _line("def456", "2026-07-17T11:00:00+02:00", "Jan", "fix: z"),
        ]
    )
    commits = parse_git_log(output)
    assert len(commits) == 2
    assert commits[0].sha == "abc123"
    assert commits[0].message == "feat: x, y"  # przecinek w komunikacie nie psuje pól
    assert commits[0].timestamp.utcoffset() is not None  # świadomy


def test_parse_git_log_skips_malformed() -> None:
    output = "linia bez separatorów\n" + _line("s", "2026-07-17T10:00:00+02:00", "a", "m")
    commits = parse_git_log(output)
    assert len(commits) == 1


def test_parse_git_log_skips_bad_timestamp() -> None:
    assert parse_git_log(_line("s", "niedata", "a", "m")) == []


def _fake_git(
    monkeypatch: pytest.MonkeyPatch, *, stdout: str | None, calls: list[dict[str, Any]]
) -> None:
    """Podstaw ``subprocess.run``: ``rev-parse`` mówi „to repo", reszta oddaje zadany ``stdout``."""

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"cmd": cmd, **kwargs})
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="true\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr=None)

    monkeypatch.setattr(git_log.subprocess, "run", _run)


def test_git_is_decoded_with_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """REGRESJA: ścisłe UTF-8 wywracało bieg na repo z i18n.commitEncoding=ISO-8859-2."""
    calls: list[dict[str, Any]] = []
    _fake_git(monkeypatch, stdout="", calls=calls)
    run_git_log(tmp_path, since="a", until="b", author="")
    assert all(call["errors"] == "replace" for call in calls)


def test_empty_stdout_does_not_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """REGRESJA: na Windows ``stdout`` bywa ``None``, a strażnik ``returncode`` tego nie łapie."""
    calls: list[dict[str, Any]] = []
    _fake_git(monkeypatch, stdout=None, calls=calls)
    assert resolve_author(tmp_path) == ""
    assert run_git_log(tmp_path, since="a", until="b", author="") == []


def test_missing_repo_and_failed_log_exit_readably(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SystemExit, match="nie istnieje"):
        resolve_author(tmp_path / "nie-ma")

    def _fail(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="true\n", stderr="")
        return subprocess.CompletedProcess(cmd, 128, stdout=None, stderr=None)

    monkeypatch.setattr(git_log.subprocess, "run", _fail)
    with pytest.raises(SystemExit, match="git log nie powiódł się"):
        run_git_log(tmp_path, since="a", until="b", author="")


def test_missing_git_binary_exits_readably(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_git(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("git")

    monkeypatch.setattr(git_log.subprocess, "run", _no_git)
    with pytest.raises(SystemExit, match="git"):
        resolve_author(tmp_path)
