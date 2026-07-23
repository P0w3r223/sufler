"""Parsowanie wyjścia ``git log`` — czysto, bez uruchamiania gita."""

from __future__ import annotations

from claude_summary.adapters.git_log import parse_git_log

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
