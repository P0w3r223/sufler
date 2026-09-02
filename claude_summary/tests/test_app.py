"""Integracja CLI: bramka zgody (fail-closed), bieg bez repo, zapis plików i start ``main``."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from claude_summary import app as app_module
from claude_summary.app import _Args, _with_prose, _write_files, main, run
from claude_summary.config import Settings
from claude_summary.core.models import Commit, DaySummary, Prompt, SummaryReport


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


def test_granice_zakresu_ida_do_gita_ZE_STREFA_nie_naiwnym_napisem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regresja: naiwny napis git czyta w strefie PROCESU, a grupowanie idzie w `settings.tz`.

    Na hoście w UTC (kontener, maszyna CI) „2026-09-02 00:00:00" znaczyło 02:00 czasu
    warszawskiego, więc commity z pierwszych dwóch godzin pierwszej doby zakresu wypadały
    z raportu bez żadnego śladu. To narzędzie jest materiałem dowodowym dla worklogu Jira, więc
    zgubiony commit to zaniżony czas pracy.
    """
    from claude_summary.adapters import git_log

    zapytania: dict[str, str] = {}

    def _spy(repo, *, since: str, until: str, author: str):  # noqa: ANN001, ANN202
        zapytania.update(since=since, until=until)
        return []

    monkeypatch.setattr(app_module.git_log, "run_git_log", _spy)
    monkeypatch.setattr(git_log, "resolve_author", lambda _repo: "kto@example.com")

    settings = _settings(tmp_path, consent=True)
    args = _Args(repo=tmp_path, since=date(2026, 9, 2), until=date(2026, 9, 2))
    run(args, settings)

    # Offset strefy raportu MUSI być w obu granicach — inaczej git liczy dobę gdzie indziej.
    assert zapytania["since"].startswith("2026-09-02T00:00:00+")
    assert zapytania["until"].startswith("2026-09-02T23:59:59")
    assert "+" in zapytania["until"]


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


def test_missing_projects_dir_warns_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESJA: zły katalog projektów dawał raport z zerami i ciche wyjście 0."""
    code = run(_Args(consent=True), _settings(tmp_path))
    assert code == 0
    assert "nie istnieje" in capsys.readouterr().err


def _report() -> SummaryReport:
    prompt = Prompt(
        timestamp=datetime(2026, 7, 20, 7, tzinfo=timezone.utc),
        text="zrobiłem X",
        session_id="sess",
        cwd="",
        project="p",
    )
    commit = Commit(
        sha="a1b2c3d4e5",
        timestamp=datetime(2026, 7, 20, 8, tzinfo=timezone.utc),
        author="Jan",
        message="feat: x",
    )
    day = DaySummary(day=date(2026, 7, 20), prompts=(prompt,), commits=(commit,))
    return SummaryReport(
        person="Jan",
        since=date(2026, 7, 20),
        until=date(2026, 7, 20),
        repo=None,
        days=(day,),
    )


def test_out_with_dotted_name_keeps_the_date(tmp_path: Path) -> None:
    """REGRESJA: --out raport.2026-07-20 i raport.2026-07-19 pisały do tego samego pliku."""
    settings = _settings(tmp_path)
    for day in ("2026-07-19", "2026-07-20"):
        _write_files(
            _report(), md=f"# {day}", js=None, out=tmp_path / f"raport.{day}", settings=settings
        )
    assert (tmp_path / "raport.2026-07-19.md").read_text(encoding="utf-8") == "# 2026-07-19"
    assert (tmp_path / "raport.2026-07-20.md").read_text(encoding="utf-8") == "# 2026-07-20"


def test_out_with_own_suffix_is_not_doubled(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_files(_report(), md="# md", js="{}", out=tmp_path / "raport.md", settings=settings)
    assert (tmp_path / "raport.md").exists()
    assert (tmp_path / "raport.json").exists()
    assert not (tmp_path / "raport.md.md").exists()


def test_write_files_without_out_uses_output_dir(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_files(_report(), md="# md", js="{}", out=None, settings=settings)
    stem = tmp_path / "out" / "summary_2026-07-20_2026-07-20"
    assert stem.with_suffix(".md").exists()
    assert stem.with_suffix(".json").exists()


def test_with_prose_without_api_key_notes_and_degrades(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    days = list(_report().days)
    result = _with_prose(days, settings=_settings(tmp_path), person="Jan")
    assert result == days  # dane strukturalne bez zmian
    assert "brak klucza API" in capsys.readouterr().err


def test_with_prose_degrades_single_failing_day(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(day: DaySummary, *, person: str, llm: object) -> str:
        raise RuntimeError("model padł")

    monkeypatch.setattr(app_module, "summarize_day", _boom)
    days = list(_report().days)
    result = _with_prose(days, settings=_settings(tmp_path, api_key="sk-test"), person="Jan")
    assert [day.llm_prose for day in result] == [None]
    assert "model padł" in capsys.readouterr().err


def test_main_reports_broken_config_without_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    """REGRESJA: konfiguracja czytana przed parsowaniem argumentów sypała ValueError."""
    monkeypatch.setattr(app_module.env, "load_dotenv", lambda: None)
    monkeypatch.setenv("CLAUDE_SUMMARY_DEFAULT_DAYS", "dużo")
    monkeypatch.setattr(sys, "argv", ["claude-summary", "--consent"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert "Błąd konfiguracji" in str(exc.value)


def test_main_help_works_despite_broken_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(app_module.env, "load_dotenv", lambda: None)
    monkeypatch.setenv("CLAUDE_SUMMARY_TZ", "Mars/Olympus")
    monkeypatch.setattr(sys, "argv", ["claude-summary", "--help"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert "Użycie:" in capsys.readouterr().out
