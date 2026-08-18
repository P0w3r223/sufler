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


def _private_report() -> SummaryReport:
    """Raport z metadanymi, które NIE mogą wyjść na zewnątrz: ścieżka z nazwą użytkownika,
    adres e-mail osoby i autora commita, pełny identyfikator sesji."""
    prompt = Prompt(
        timestamp=datetime(2026, 7, 17, 7, 0, tzinfo=timezone.utc),
        text="zrób X",
        session_id="3534249a-6d2c-480a-8f8b-a0c746180c71",
        cwd="",
        project="C--Users-[UŻYTKOWNIK]-repo",
    )
    commit = Commit(
        sha="abcdef1234567890",
        timestamp=datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc),
        author="jan.kowalski@firma.pl",
        message="feat: x",
    )
    return SummaryReport(
        person="jan.kowalski@firma.pl",
        since=date(2026, 7, 17),
        until=date(2026, 7, 17),
        repo="C:\\Users\\Jan Kowalski\\repo",
        days=(DaySummary(day=date(2026, 7, 17), prompts=(prompt,), commits=(commit,)),),
    )


def test_json_metadata_is_redacted() -> None:
    # REGRESJA: repo, session_id i osoba omijały redakcję i wychodziły w kontrakcie JSON.
    payload = to_json(_private_report(), tz=WARSAW)
    assert "Kowalski\\\\repo" not in payload  # ścieżka bez nazwy użytkownika
    assert "jan.kowalski@firma.pl" not in payload
    assert "3534249a-6d2c-480a-8f8b-a0c746180c71" not in payload

    data = to_dict(_private_report(), tz=WARSAW)
    assert data["person"] == "Jan Kowalski"
    assert data["days"][0]["commits"][0]["author"] == "Jan Kowalski"
    assert data["days"][0]["prompts"][0]["session_id"] == "3534249a"
    assert "[UŻYTKOWNIK]" in str(data["repo"])


def test_markdown_metadata_is_redacted() -> None:
    md = to_markdown(_private_report(), tz=WARSAW)
    assert "jan.kowalski@firma.pl" not in md
    assert "Jan Kowalski\\repo" not in md
    assert "# Podsumowanie aktywności — Jan Kowalski" in md
    assert "[UŻYTKOWNIK]" in md


def test_markdown_has_headers_and_empty_note() -> None:
    md = to_markdown(_report(), tz=WARSAW)
    assert "# Podsumowanie aktywności — me" in md
    assert "## 2026-07-17 (piątek)" in md
    assert "Brak zarejestrowanej aktywności." in md
    assert "`abcdef1`" in md
