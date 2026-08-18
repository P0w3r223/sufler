"""Odczyt transkryptów z dysku: bramka zgody na granicy, korelacja repo↔sesja, ostrzeżenia."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from claude_summary.adapters.transcript_files import (
    _Counters,
    _folder_name_of,
    _iter_prompts,
    iter_prompts,
    repo_folder_name,
    scan_prompts,
)
from claude_summary.core.consent import ConsentError, ConsentProof, grant_consent

CONSENT = grant_consent(flag=True, env_consent=False)
assert CONSENT is not None


def _write_jsonl(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries),
        encoding="utf-8",
    )


def _human(content: str, cwd: str, timestamp: str = "2026-07-17T09:00:00.000Z") -> dict[str, Any]:
    return {
        "type": "user",
        "promptSource": "typed",
        "origin": {"kind": "human"},
        "isSidechain": False,
        "sessionId": "s",
        "cwd": cwd,
        "timestamp": timestamp,
        "message": {"role": "user", "content": content},
    }


def test_iter_prompts_keeps_only_human(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    _write_jsonl(
        projects / "C--Users-Test-repo" / "sess.jsonl",
        [
            _human("prawdziwy prompt ąćź", "C:\\Users\\Test\\repo"),
            {
                "type": "user",
                "promptSource": None,
                "toolUseResult": "x",
                "message": {"role": "user", "content": [{"type": "tool_result"}]},
            },
            {"type": "ai-title", "title": "zaślepka metadanych"},
            {"type": "assistant", "message": {"role": "assistant", "content": []}},
        ],
    )
    prompts = list(iter_prompts(projects, consent=CONSENT))
    assert len(prompts) == 1
    assert prompts[0].text == "prawdziwy prompt ąćź"  # UTF-8 zachowane


def test_read_without_consent_proof_is_refused(tmp_path: Path) -> None:
    """SEDNO: bramka zgody stoi na GRANICY odczytu, nie tylko w orkiestratorze."""
    projects = tmp_path / "projects"
    _write_jsonl(projects / "C--x" / "s.jsonl", [_human("prywatny prompt", "C:\\x")])

    with pytest.raises(ConsentError):
        list(iter_prompts(projects, consent=None))  # type: ignore[arg-type]
    with pytest.raises(ConsentError):
        scan_prompts(projects, consent=True)  # type: ignore[arg-type]
    # Dowodu nie da się zbudować obok bramki.
    with pytest.raises(ConsentError):
        ConsentProof()
    assert grant_consent(flag=False, env_consent=False) is None


def test_internal_reader_also_carries_the_proof(tmp_path: Path) -> None:
    """Dowód idzie aż do właściwego przebiegu — wołający wewnątrz modułu też nie obejdzie bramki."""
    projects = tmp_path / "projects"
    _write_jsonl(projects / "C--x" / "s.jsonl", [_human("prywatny prompt", "C:\\x")])
    reader = _iter_prompts(
        projects,
        _Counters(),
        consent=None,  # type: ignore[arg-type]
        repo=None,
        all_projects=False,
        only_project=None,
    )
    with pytest.raises(ConsentError):
        list(reader)


def test_folder_name_of_replaces_windows_separators() -> None:
    # Czysta transformacja stringa (bez .resolve()) — platformowo niezależna, w odróżnieniu od
    # repo_folder_name z surową ścieżką windowsową (patrz test niżej: pęka na POSIX, bo
    # .resolve() dokleja cwd do backslashy potraktowanych jako literalna nazwa pliku).
    assert _folder_name_of("C:\\Users\\Test\\repo") == "C--Users-Test-repo"


def test_repo_folder_name_resolves_real_path(tmp_path: Path) -> None:
    repo = tmp_path / "myrepo"
    repo.mkdir()

    assert repo_folder_name(repo) == _folder_name_of(str(repo.resolve()))


def test_repo_filter_by_cwd_and_folder(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    repo = tmp_path / "myrepo"
    repo.mkdir()
    exact_name = repo_folder_name(repo)
    # Sesja w folderze pasującym nazwą do repo, ale cwd gdzie indziej → liczy się (start w repo).
    _write_jsonl(
        projects / exact_name / "a.jsonl",
        [_human("w exact folderze", "C:\\gdzie\\indziej")],
    )
    # Sesja w innym folderze, ale cwd w drzewie repo → liczy się (worktree/podkatalog).
    _write_jsonl(projects / "C--other" / "b.jsonl", [_human("cwd w repo", str(repo / "sub"))])
    # Sesja w innym folderze z cwd poza repo → odrzucona.
    _write_jsonl(projects / "C--other2" / "c.jsonl", [_human("obcy projekt", "C:\\obcy")])
    texts = {prompt.text for prompt in iter_prompts(projects, consent=CONSENT, repo=repo)}
    assert texts == {"w exact folderze", "cwd w repo"}


def test_only_project_restricts_folder(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    _write_jsonl(projects / "C--one" / "a.jsonl", [_human("jeden", "C:\\x")])
    _write_jsonl(projects / "C--two" / "b.jsonl", [_human("dwa", "C:\\y")])
    texts = {
        prompt.text for prompt in iter_prompts(projects, consent=CONSENT, only_project="C--one")
    }
    assert texts == {"jeden"}


def test_scan_warns_when_projects_dir_missing(tmp_path: Path) -> None:
    result = scan_prompts(tmp_path / "nie-ma", consent=CONSENT)
    assert result.prompts == ()
    assert any("nie istnieje" in warning for warning in result.warnings)


def test_scan_warns_when_no_project_folders(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    result = scan_prompts(projects, consent=CONSENT)
    assert any("nie ma żadnego folderu" in warning for warning in result.warnings)


def test_scan_warns_when_only_project_matches_nothing(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    _write_jsonl(projects / "C--one" / "a.jsonl", [_human("jeden", "C:\\x")])
    result = scan_prompts(projects, consent=CONSENT, only_project="C--brak")
    assert any("--project" in warning for warning in result.warnings)


def test_scan_warns_when_discriminator_rejects_every_user_line(tmp_path: Path) -> None:
    """REGRESJA: dryf formatu transkryptu dawał raport z zerami i cichy exit 0."""
    projects = tmp_path / "projects"
    drifted = _human("prompt po zmianie formatu", "C:\\x")
    drifted["promptSource"] = "nowa-wartosc"
    _write_jsonl(projects / "C--one" / "a.jsonl", [drifted, drifted])
    result = scan_prompts(projects, consent=CONSENT)
    assert result.prompts == ()
    assert any("dyskryminatora" in warning for warning in result.warnings)


def test_scan_warns_when_nothing_matches_repo(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _write_jsonl(projects / "C--other" / "a.jsonl", [_human("obcy projekt", "C:\\obcy")])
    result = scan_prompts(projects, consent=CONSENT, repo=repo)
    assert result.prompts == ()
    assert any("--all-projects" in warning for warning in result.warnings)


def test_scan_counts_broken_json_lines(tmp_path: Path) -> None:
    """REGRESJA: uszkodzone linie znikały bez śladu."""
    projects = tmp_path / "projects"
    path = projects / "C--one" / "a.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "{niepoprawny json\n" + json.dumps(_human("dobry prompt", "C:\\x")) + "\n{znowu zły\n",
        encoding="utf-8",
    )
    result = scan_prompts(projects, consent=CONSENT)
    assert [prompt.text for prompt in result.prompts] == ["dobry prompt"]
    assert any("2 uszkodzonych linii JSON" in warning for warning in result.warnings)


def test_scan_counts_unreadable_transcripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = tmp_path / "projects"
    _write_jsonl(projects / "C--one" / "a.jsonl", [_human("jeden", "C:\\x")])
    original = Path.open

    def _deny(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.suffix == ".jsonl":
            raise PermissionError(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _deny)
    result = scan_prompts(projects, consent=CONSENT)
    assert result.prompts == ()
    assert any("nieczytelnych plików" in warning for warning in result.warnings)
