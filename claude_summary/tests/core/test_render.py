"""Render do JSON i Markdown — kształt kontraktu dla agenta i czytelność digestu."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from claude_summary.core.models import Commit, DaySummary, Prompt, SummaryReport
from claude_summary.core.render import to_dict, to_json, to_markdown

WARSAW = ZoneInfo("Europe/Warsaw")


def _report() -> SummaryReport:
    prompt = Prompt(
        timestamp=datetime(2026, 7, 17, 7, 0, tzinfo=timezone.utc),
        text="a\n b",
        session_id="s",
        cwd="",
        project="proj",
    )
    commit = Commit(
        sha="abcdef1234567890",
        timestamp=datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc),
        author="me",
        message="feat: x",
    )
    day = DaySummary(day=date(2026, 7, 17), prompts=(prompt,), commits=(commit,))
    empty = DaySummary(day=date(2026, 7, 18), prompts=(), commits=())
    return SummaryReport(
        person="me",
        since=date(2026, 7, 17),
        until=date(2026, 7, 18),
        repo="C:\\repo",
        days=(day, empty),
    )


def test_to_dict_shape() -> None:
    data = to_dict(_report(), tz=WARSAW)
    assert data["person"] == "me"
    assert data["timezone"] == "Europe/Warsaw"
    assert len(data["days"]) == 2
    first = data["days"][0]
    assert first["weekday"] == "piątek"
    assert first["prompt_count"] == 1
    assert first["commits"][0]["short_sha"] == "abcdef1"
    assert first["prompts"][0]["time"] == "09:00"  # 07:00Z + 2h (CEST)


def test_to_json_roundtrips() -> None:
    parsed = json.loads(to_json(_report(), tz=WARSAW))
    assert parsed["days"][1]["prompt_count"] == 0


def test_markdown_has_headers_and_empty_note() -> None:
    md = to_markdown(_report(), tz=WARSAW)
    assert "# Podsumowanie aktywności — me" in md
    assert "## 2026-07-17 (piątek)" in md
    assert "Brak zarejestrowanej aktywności." in md
    assert "`abcdef1`" in md
