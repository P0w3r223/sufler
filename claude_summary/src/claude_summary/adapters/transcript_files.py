"""Odczyt transkryptów Claude Code z ``~/.claude/projects`` i korelacja z repozytorium.

Każdy folder projektu nazywa się jak absolutna ścieżka katalogu startu sesji, z separatorami
zamienionymi na ``-`` (``C:\\Users\\X\\repo`` → ``C--Users-X-repo``). Sesja może jednak działać
w podkatalogu (``cwd``) albo w worktree, więc przy wskazanym repo bierzemy: folder o pasującej
nazwie W CAŁOŚCI oraz prompty z innych folderów, których ``cwd`` leży w drzewie repo. Bez repo —
wszystkie foldery. Linie parsujemy leniwie i odpornie: uszkodzona linia jest pomijana, a pliki
tylko z metadanymi (``ai-title``…) naturalnie odpada na dyskryminatorze ``is_human_prompt``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from claude_summary.core.models import Prompt
from claude_summary.core.transcripts import is_human_prompt, parse_prompt


def _folder_name_of(text: str) -> str:
    """Zamień separatory ścieżki na ``-`` (czysta transformacja stringa, bez dotyku dysku)."""
    for separator in (":", "\\", "/"):
        text = text.replace(separator, "-")
    return text


def repo_folder_name(repo: Path) -> str:
    """Nazwa folderu ``~/.claude/projects`` odpowiadająca katalogowi startu sesji w tym repo."""
    return _folder_name_of(str(repo.resolve()))


def _is_under(cwd: str, repo: Path) -> bool:
    """Czy ``cwd`` leży w drzewie ``repo`` (porównanie odporne na wielkość liter na Windows)."""
    if not cwd:
        return False
    try:
        child = os.path.normcase(os.path.abspath(cwd))
        root = os.path.normcase(os.path.abspath(str(repo)))
    except (ValueError, OSError):
        return False
    return child == root or child.startswith(root + os.sep)


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Strumień słowników z pliku JSONL (UTF-8); uszkodzone/puste linie pomijane."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def iter_prompts(
    projects_dir: Path,
    *,
    repo: Path | None = None,
    all_projects: bool = False,
    only_project: str | None = None,
) -> Iterator[Prompt]:
    """Wygeneruj realne prompty człowieka ze wszystkich pasujących sesji.

    - ``only_project`` — czytaj wyłącznie ten folder (bez filtra ``cwd``).
    - ``all_projects`` lub brak ``repo`` — wszystkie foldery, bez filtra ``cwd``.
    - w innym razie: folder o nazwie z ``repo`` w całości + prompty z ``cwd`` w drzewie ``repo``.
    """
    if not projects_dir.is_dir():
        return
    exact = repo_folder_name(repo) if repo is not None else None
    for folder in sorted(projects_dir.iterdir()):
        if not folder.is_dir():
            continue
        if only_project is not None and folder.name != only_project:
            continue
        for transcript in sorted(folder.glob("*.jsonl")):
            for line in _read_jsonl(transcript):
                if not is_human_prompt(line):
                    continue
                prompt = parse_prompt(line, project=folder.name)
                if prompt is None:
                    continue
                if (
                    only_project is not None
                    or all_projects
                    or repo is None
                    or folder.name == exact
                    or _is_under(prompt.cwd, repo)
                ):
                    yield prompt
