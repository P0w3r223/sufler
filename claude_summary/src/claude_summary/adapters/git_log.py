"""Odczyt commitów przez ``git log`` — jedyne miejsce z ``subprocess`` w pakiecie.

Parsowanie wydzielone do czystej ``parse_git_log`` (testowanej bez gita); cienki ``run_git_log``
tylko woła proces. Argumenty przekazujemy jako listę (bez ``shell=True``), a pola rozdzielamy
znakiem jednostkowym ``\\x1f``, więc przecinki/spacje w komunikacie commita nie psują parsowania.
Błędy (brak gita, nie-repo, niezerowy kod) kończą się czytelnym ``SystemExit``, nie tracebackiem.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from claude_summary.core.models import Commit
from claude_summary.core.redaction import redact_text
from claude_summary.core.timeutil import parse_iso

_SEP = "\x1f"
_PRETTY = f"format:%H{_SEP}%aI{_SEP}%an{_SEP}%s"


def parse_git_log(output: str) -> list[Commit]:
    """Sparsuj wyjście ``git log --pretty=format:%H\\x1f%aI\\x1f%an\\x1f%s`` na listę commitów."""
    commits: list[Commit] = []
    for raw in output.splitlines():
        if not raw.strip():
            continue
        parts = raw.split(_SEP)
        if len(parts) != 4:
            continue
        sha, timestamp_raw, author, subject = parts
        try:
            timestamp = parse_iso(timestamp_raw)
        except ValueError:
            continue
        commits.append(
            Commit(
                sha=sha.strip(),
                timestamp=timestamp,
                author=author.strip(),
                message=redact_text(subject.strip()),  # komunikat też może nieść sekret/IP
            )
        )
    return commits


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError as exc:
        raise SystemExit("Nie znaleziono polecenia 'git' w PATH — zainstaluj Git.") from exc


def _ensure_repo(repo: Path) -> None:
    if not repo.exists():
        raise SystemExit(f"Wskazane repo nie istnieje: {repo}")
    result = _run_git(repo, "rev-parse", "--is-inside-work-tree")
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise SystemExit(f"To nie jest repozytorium git: {repo}")


def resolve_author(repo: Path) -> str:
    """Domyślny autor do filtrowania commitów: ``git config user.email`` w repo (może być pusty)."""
    _ensure_repo(repo)
    result = _run_git(repo, "config", "user.email")
    return result.stdout.strip() if result.returncode == 0 else ""


def run_git_log(repo: Path, *, since: str, until: str, author: str) -> list[Commit]:
    """Pobierz commity z zakresu ``since..until``; pusty ``author`` = bez filtra autora."""
    _ensure_repo(repo)
    args = ["log", f"--since={since}", f"--until={until}", f"--pretty={_PRETTY}"]
    if author:
        args.append(f"--author={author}")
    result = _run_git(repo, *args)
    if result.returncode != 0:
        raise SystemExit(f"git log nie powiódł się dla {repo}: {result.stderr.strip()}")
    return parse_git_log(result.stdout)
