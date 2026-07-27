"""Parser przysłanego claude_summary + bramka właściciela (self-service, ADR 0037 reg. 3)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from workmate.core.domain.submitted_summary import (
    SubmissionRejected,
    parse_submitted_summary,
    verify_submission_owner,
)
from workmate.core.domain.timesheet import Person


def _day(
    date_str: str,
    *,
    prose: str | None = None,
    commits: tuple[str, ...] = (),
    prompts: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "date": date_str,
        "llm_prose": prose,
        "commits": [{"message": m} for m in commits],
        "prompts": [{"text": t} for t in prompts],
    }


def _payload(
    days: list[dict[str, Any]],
    *,
    person: str = "me@x.pl",
    since: str = "2026-07-13",
    until: str = "2026-07-19",
) -> dict[str, Any]:
    return {"person": person, "since": since, "until": until, "days": days}


def _person(git_email: str = "me@x.pl") -> Person:
    return Person(source_id="EMP-1", aad_user_id="aad-1", jira_user="me@x.pl", git_email=git_email)


# --- parsowanie kluczy i komentarzy ----------------------------------------------


def test_extracts_issue_keys_and_comment_per_day() -> None:
    summary = parse_submitted_summary(
        _payload(
            [
                _day("2026-07-15", prose="Pracował nad WT-1.", commits=("WT-1 rano", "WT-2 po")),
                _day("2026-07-16", commits=("fix: WT-3",)),
            ]
        )
    )
    assert summary.person_git_email == "me@x.pl"
    assert summary.since == date(2026, 7, 13)
    assert summary.until == date(2026, 7, 19)
    assert summary.issue_keys_by_day == {
        date(2026, 7, 15): ("WT-1", "WT-2"),
        date(2026, 7, 16): ("WT-3",),
    }
    assert summary.comments_by_day[date(2026, 7, 15)] == "Pracował nad WT-1."
    assert summary.comments_by_day[date(2026, 7, 16)] == "fix: WT-3"


def test_person_is_lowercased() -> None:
    summary = parse_submitted_summary(_payload([], person="Me@X.PL"))
    assert summary.person_git_email == "me@x.pl"


def test_issue_keys_deduped_in_order() -> None:
    summary = parse_submitted_summary(
        _payload([_day("2026-07-15", commits=("WT-1 a", "WT-1 b", "WT-2 c"))])
    )
    assert summary.issue_keys_by_day == {date(2026, 7, 15): ("WT-1", "WT-2")}


def test_day_without_keys_absent_from_keys_map_but_may_have_comment() -> None:
    summary = parse_submitted_summary(_payload([_day("2026-07-15", commits=("bez klucza",))]))
    assert summary.issue_keys_by_day == {}
    assert summary.comments_by_day[date(2026, 7, 15)] == "bez klucza"


def test_empty_week_is_valid_not_rejected() -> None:
    summary = parse_submitted_summary(_payload([]))
    assert summary.issue_keys_by_day == {}
    assert summary.comments_by_day == {}


def test_null_commit_message_not_the_string_none() -> None:
    summary = parse_submitted_summary(
        _payload(
            [
                {
                    "date": "2026-07-15",
                    "llm_prose": None,
                    "commits": [{"message": None}, {"message": "feat: realny"}],
                    "prompts": [],
                }
            ]
        )
    )
    assert summary.comments_by_day[date(2026, 7, 15)] == "feat: realny"


def test_bad_day_shape_is_skipped_others_read() -> None:
    summary = parse_submitted_summary(
        _payload(["nie-slownik", {"date": "nie-data"}, _day("2026-07-15", prose="realny")])  # type: ignore[list-item]
    )
    assert list(summary.comments_by_day) == [date(2026, 7, 15)]


# --- okno since/until -------------------------------------------------------------


def test_missing_window_rejected() -> None:
    with pytest.raises(SubmissionRejected, match="since"):
        parse_submitted_summary({"person": "me@x.pl", "days": []})


def test_reversed_window_rejected() -> None:
    with pytest.raises(SubmissionRejected, match="odwrócone"):
        parse_submitted_summary(_payload([], since="2026-07-19", until="2026-07-13"))


# --- twarde odrzucenia kształtu ---------------------------------------------------


def test_non_dict_payload_rejected() -> None:
    with pytest.raises(SubmissionRejected):
        parse_submitted_summary(["nie", "obiekt"])


def test_missing_person_rejected() -> None:
    with pytest.raises(SubmissionRejected):
        parse_submitted_summary({"since": "2026-07-13", "until": "2026-07-19", "days": []})


def test_days_not_a_list_rejected() -> None:
    with pytest.raises(SubmissionRejected):
        parse_submitted_summary(
            {"person": "me@x.pl", "since": "2026-07-13", "until": "2026-07-19", "days": {}}
        )


# --- bramka właściciela (cross-check nadawcy) -------------------------------------


def test_owner_match_passes() -> None:
    verify_submission_owner(_person("me@x.pl"), "me@x.pl")  # nie rzuca


def test_owner_match_is_case_insensitive() -> None:
    verify_submission_owner(_person("Me@X.pl"), "me@x.PL")  # nie rzuca


def test_owner_mismatch_rejected() -> None:
    with pytest.raises(SubmissionRejected, match="cudzego eksportu"):
        verify_submission_owner(_person("me@x.pl"), "kolega@x.pl")


def test_sender_without_git_email_rejected_fail_closed() -> None:
    with pytest.raises(SubmissionRejected, match="git_email"):
        verify_submission_owner(_person(git_email=""), "me@x.pl")
