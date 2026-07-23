"""Odczyt transkryptów z dysku i korelacja repo↔sesja (folder + filtr cwd)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claude_summary.adapters.transcript_files import iter_prompts, repo_folder_name


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
    prompts = list(iter_prompts(projects))
    assert len(prompts) == 1
    assert prompts[0].text == "prawdziwy prompt ąćź"  # UTF-8 zachowane


def test_repo_folder_name_windows() -> None:
    assert repo_folder_name(Path("C:\\Users\\Test\\repo")) == "C--Users-Test-repo"


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
    texts = {prompt.text for prompt in iter_prompts(projects, repo=repo)}
    assert texts == {"w exact folderze", "cwd w repo"}


def test_only_project_restricts_folder(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    _write_jsonl(projects / "C--one" / "a.jsonl", [_human("jeden", "C:\\x")])
    _write_jsonl(projects / "C--two" / "b.jsonl", [_human("dwa", "C:\\y")])
    texts = {prompt.text for prompt in iter_prompts(projects, only_project="C--one")}
    assert texts == {"jeden"}
