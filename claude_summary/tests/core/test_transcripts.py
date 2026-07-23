"""Dyskryminator promptu i parsowanie — sedno odsiewania realnych wpisów człowieka."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from claude_summary.core.transcripts import is_human_prompt, parse_prompt, strip_injected

Line = Callable[..., dict[str, Any]]


def test_genuine_typed_prompt_is_human(make_user_line: Line) -> None:
    assert is_human_prompt(make_user_line()) is True


def test_tool_result_excluded(make_user_line: Line) -> None:
    line = make_user_line(
        promptSource=None,
        toolUseResult="wynik",
        message={"role": "user", "content": [{"type": "tool_result", "content": "x"}]},
    )
    assert is_human_prompt(line) is False


def test_task_notification_excluded(make_user_line: Line) -> None:
    line = make_user_line(
        promptSource="system",
        origin={"kind": "task-notification"},
        message={"role": "user", "content": "<task-notification>...</task-notification>"},
    )
    assert is_human_prompt(line) is False


def test_slash_command_excluded(make_user_line: Line) -> None:
    line = make_user_line(
        promptSource=None,
        origin=None,
        message={"role": "user", "content": "<command-name>/compact</command-name>"},
    )
    assert is_human_prompt(line) is False


def test_sidechain_excluded(make_user_line: Line) -> None:
    assert is_human_prompt(make_user_line(isSidechain=True)) is False


def test_assistant_line_excluded(make_user_line: Line) -> None:
    assert is_human_prompt(make_user_line(type="assistant")) is False


def test_suggestion_accepted_is_human(make_user_line: Line) -> None:
    assert is_human_prompt(make_user_line(promptSource="suggestion_accepted")) is True


def test_non_string_content_excluded(make_user_line: Line) -> None:
    line = make_user_line(message={"role": "user", "content": [{"type": "text", "text": "x"}]})
    assert is_human_prompt(line) is False


def test_parse_prompt_reads_text_and_utc(make_user_line: Line) -> None:
    line = make_user_line(message={"role": "user", "content": "  zrób Y  "})
    prompt = parse_prompt(line, project="proj")
    assert prompt is not None
    assert prompt.text == "zrób Y"
    assert prompt.project == "proj"
    assert prompt.timestamp.utcoffset() == timedelta(0)  # świadomy, UTC


def test_parse_prompt_strips_system_reminder(make_user_line: Line) -> None:
    content = "prawdziwy prompt\n<system-reminder>doklejone przez harness</system-reminder>"
    line = make_user_line(message={"role": "user", "content": content})
    prompt = parse_prompt(line, project="p")
    assert prompt is not None
    assert prompt.text == "prawdziwy prompt"
    assert "system-reminder" not in prompt.text


def test_parse_prompt_none_for_empty(make_user_line: Line) -> None:
    line = make_user_line(message={"role": "user", "content": "   "})
    assert parse_prompt(line, project="p") is None


def test_strip_injected_removes_command_block() -> None:
    assert strip_injected("A<command-args>x</command-args>B") == "AB"
