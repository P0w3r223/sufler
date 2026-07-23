"""Komentarz dnia: proza LLM > commity > prompty, zwijanie i przycinanie (ADR 0036)."""

from __future__ import annotations

from workmate.core.domain.day_comment import build_comment


def test_llm_prose_is_preferred() -> None:
    result = build_comment(
        llm_prose="Pracował nad filtrem.", commit_messages=["feat: y"], prompt_texts=["z"]
    )
    assert result == "Pracował nad filtrem."


def test_commit_messages_when_no_prose() -> None:
    result = build_comment(
        llm_prose=None, commit_messages=["feat: filtr", "fix: bug"], prompt_texts=["x"]
    )
    assert result == "feat: filtr; fix: bug"


def test_prompts_when_no_prose_or_commits_capped_to_three() -> None:
    assert (
        build_comment(
            llm_prose="",
            commit_messages=[],
            prompt_texts=["napraw testy", "dodaj X", "usuń Y", "czwarty"],
        )
        == "napraw testy; dodaj X; usuń Y"
    )


def test_blank_prose_falls_through_to_commits() -> None:
    assert build_comment(llm_prose="   ", commit_messages=["feat: a"], prompt_texts=[]) == "feat: a"


def test_empty_inputs_give_empty_comment() -> None:
    assert build_comment(llm_prose=None, commit_messages=[], prompt_texts=[]) == ""


def test_blank_entries_are_skipped() -> None:
    assert (
        build_comment(llm_prose=None, commit_messages=["", "   "], prompt_texts=["", "realny"])
        == "realny"
    )


def test_whitespace_is_collapsed() -> None:
    assert (
        build_comment(llm_prose=None, commit_messages=["feat:   wiele   spacji\n"], prompt_texts=[])
        == "feat: wiele spacji"
    )


def test_comment_is_capped_to_max_len() -> None:
    result = build_comment(llm_prose="x" * 600, commit_messages=[], prompt_texts=[], max_len=100)
    assert len(result) == 100
    assert result.endswith("…")
