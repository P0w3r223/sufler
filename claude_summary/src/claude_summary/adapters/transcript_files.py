"""Odczyt transkryptów Claude Code z ``~/.claude/projects`` i korelacja z repozytorium.

Każdy folder projektu nazywa się jak absolutna ścieżka katalogu startu sesji, z separatorami
zamienionymi na ``-`` (``C:\\Users\\X\\repo`` → ``C--Users-X-repo``). Sesja może jednak działać
w podkatalogu (``cwd``) albo w worktree, więc przy wskazanym repo bierzemy: folder o pasującej
nazwie W CAŁOŚCI oraz prompty z innych folderów, których ``cwd`` leży w drzewie repo. Bez repo —
wszystkie foldery. Linie parsujemy leniwie i odpornie: uszkodzona linia jest pomijana, a pliki
tylko z metadanymi (``ai-title``…) naturalnie odpadają na dyskryminatorze ``is_human_prompt``.

Dwie rzeczy trzymają ten adapter uczciwym:

* **zgoda jest argumentem KAŻDEJ ścieżki odczytu** — także wewnętrznej ``_iter_prompts``, więc
  gwarancji nie zdejmie refaktor dokładający nowego wołającego: bez ``ConsentProof``
  (patrz ``core.consent``) kod się nie typuje i nie rusza;
* **pominięcia są liczone** — ``scan_prompts`` zwraca ostrzeżenia zamiast po cichu oddawać
  raport z zerami (zły katalog, dryf formatu transkryptu, brak uprawnień, uszkodzony JSON).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_summary.core.consent import ConsentProof, require_consent
from claude_summary.core.models import Prompt
from claude_summary.core.transcripts import is_human_prompt, parse_prompt


@dataclass
class _Counters:
    """Liczniki jednego przebiegu — materiał na ostrzeżenia, nie na wyjątki."""

    dir_missing: bool = False
    dir_unreadable: bool = False
    folders_seen: int = 0
    folders_matched: int = 0
    user_lines: int = 0
    human_lines: int = 0
    parsed: int = 0
    kept: int = 0
    bad_json_lines: int = 0
    unreadable_files: int = 0


@dataclass(frozen=True)
class ScanResult:
    """Prompty z przebiegu wraz z ostrzeżeniami dla operatora (pusty wynik ma mieć powód)."""

    prompts: tuple[Prompt, ...] = ()
    warnings: tuple[str, ...] = ()


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


def _read_jsonl(path: Path, counters: _Counters) -> Iterator[dict[str, Any]]:
    """Strumień słowników z pliku JSONL (UTF-8); uszkodzone linie i błędy I/O są ZLICZANE."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    counters.bad_json_lines += 1
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        counters.unreadable_files += 1
        return


def _folders(projects_dir: Path, counters: _Counters, only_project: str | None) -> list[Path]:
    """Foldery projektów do przejrzenia; brak katalogu/uprawnień odnotowujemy w licznikach."""
    if not projects_dir.is_dir():
        counters.dir_missing = True
        return []
    try:
        entries = sorted(entry for entry in projects_dir.iterdir() if entry.is_dir())
    except OSError:
        counters.dir_unreadable = True
        return []
    counters.folders_seen = len(entries)
    matched = [f for f in entries if only_project is None or f.name == only_project]
    counters.folders_matched = len(matched)
    return matched


def _iter_prompts(
    projects_dir: Path,
    counters: _Counters,
    *,
    consent: ConsentProof,
    repo: Path | None,
    all_projects: bool,
    only_project: str | None,
) -> Iterator[Prompt]:
    """Właściwy przebieg odczytu — dowód zgody niesie się aż tutaj, nie zostaje u wołającego."""
    require_consent(consent)
    exact = repo_folder_name(repo) if repo is not None else None
    for folder in _folders(projects_dir, counters, only_project):
        for transcript in sorted(folder.glob("*.jsonl")):
            for line in _read_jsonl(transcript, counters):
                if line.get("type") == "user":
                    counters.user_lines += 1
                if not is_human_prompt(line):
                    continue
                counters.human_lines += 1
                prompt = parse_prompt(line, project=folder.name)
                if prompt is None:
                    continue
                counters.parsed += 1
                if (
                    only_project is not None
                    or all_projects
                    or repo is None
                    or folder.name == exact
                    or _is_under(prompt.cwd, repo)
                ):
                    counters.kept += 1
                    yield prompt


def iter_prompts(
    projects_dir: Path,
    *,
    consent: ConsentProof,
    repo: Path | None = None,
    all_projects: bool = False,
    only_project: str | None = None,
) -> Iterator[Prompt]:
    """Wygeneruj realne prompty człowieka ze wszystkich pasujących sesji.

    Wymaga ``ConsentProof`` — bez dowodu zgody odczyt prywatnych transkryptów nie rusza.

    - ``only_project`` — czytaj wyłącznie ten folder (bez filtra ``cwd``).
    - ``all_projects`` lub brak ``repo`` — wszystkie foldery, bez filtra ``cwd``.
    - w innym razie: folder o nazwie z ``repo`` w całości + prompty z ``cwd`` w drzewie ``repo``.
    """
    require_consent(consent)  # eagerly: ``iter_prompts`` zwraca generator, nie jest nim samo
    return _iter_prompts(
        projects_dir,
        _Counters(),
        consent=consent,
        repo=repo,
        all_projects=all_projects,
        only_project=only_project,
    )


def _warnings(
    counters: _Counters, projects_dir: Path, *, repo: Path | None, only: str | None
) -> list[str]:
    """Zamień liczniki na komunikaty — pusty raport ma powiedzieć, DLACZEGO jest pusty."""
    out: list[str] = []
    if counters.dir_missing:
        out.append(f"katalog historii promptów nie istnieje: {projects_dir} — nic nie przeczytam.")
        return out
    if counters.dir_unreadable:
        out.append(f"brak dostępu do katalogu historii promptów: {projects_dir}.")
        return out
    if counters.folders_seen == 0:
        out.append(f"w {projects_dir} nie ma żadnego folderu projektu.")
        return out
    if counters.folders_matched == 0:
        out.append(f"żaden folder projektu nie pasuje do --project {only!r}.")
        return out
    if counters.user_lines and not counters.human_lines:
        out.append(
            f'znalazłem {counters.user_lines} linii type:"user", ale żadna nie przeszła '
            "dyskryminatora promptu człowieka — format transkryptu mógł się zmienić."
        )
    elif not counters.user_lines:
        out.append(
            f'w {counters.folders_matched} folderach nie ma ani jednej linii type:"user" '
            "— to nie wygląda na katalog transkryptów Claude Code."
        )
    elif counters.parsed and not counters.kept and repo is not None:
        out.append(
            f"żaden z {counters.parsed} promptów nie pochodzi z {repo} — użyj --all-projects "
            "albo --project, jeśli sesje startowały gdzie indziej."
        )
    if counters.bad_json_lines:
        out.append(f"pominięto {counters.bad_json_lines} uszkodzonych linii JSON.")
    if counters.unreadable_files:
        out.append(
            f"pominięto {counters.unreadable_files} nieczytelnych plików transkryptu "
            "(brak uprawnień albo błąd wejścia/wyjścia)."
        )
    return out


def scan_prompts(
    projects_dir: Path,
    *,
    consent: ConsentProof,
    repo: Path | None = None,
    all_projects: bool = False,
    only_project: str | None = None,
) -> ScanResult:
    """Przeczytaj prompty i zbierz ostrzeżenia (zerowy wynik nigdy nie jest cichy)."""
    require_consent(consent)
    counters = _Counters()
    prompts = tuple(
        _iter_prompts(
            projects_dir,
            counters,
            consent=consent,
            repo=repo,
            all_projects=all_projects,
            only_project=only_project,
        )
    )
    warnings = _warnings(counters, projects_dir, repo=repo, only=only_project)
    return ScanResult(prompts=prompts, warnings=tuple(warnings))
