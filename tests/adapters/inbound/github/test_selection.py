"""Testy czystej selekcji zdarzeń GitHub (mapowanie, self-skip, watermark) — bez sieci."""

from __future__ import annotations

import pytest

from workmate.adapters.inbound.github import selection


def _issue(number: int, *, login: str = "alice", **kw) -> dict:
    raw = {
        "number": number,
        "title": f"Issue {number}",
        "body": "opis",
        "html_url": f"http://gh/{number}",
        "user": {"login": login},
        "created_at": "2026-07-15T10:00:00Z",
        "updated_at": "2026-07-15T10:00:00Z",
    }
    raw.update(kw)
    return raw


def _comment(comment_id: int, *, login: str = "bob", issue: int = 5, **kw) -> dict:
    raw = {
        "id": comment_id,
        "body": "komentarz",
        "html_url": f"http://gh/c/{comment_id}",
        "user": {"login": login},
        "issue_url": f"http://api/repos/o/r/issues/{issue}",
        "created_at": "2026-07-15T11:00:00Z",
        "updated_at": "2026-07-15T11:00:00Z",
    }
    raw.update(kw)
    return raw


def _review(
    review_id: int, *, state: str = "APPROVED", login: str = "alice", pr: int = 12, **kw
) -> dict:
    raw = {
        "id": review_id,
        "state": state,
        "body": "recenzja",
        "html_url": f"https://github.com/o/r/pull/{pr}#pullrequestreview-{review_id}",
        "user": {"login": login},
        "submitted_at": "2026-07-15T12:00:00Z",
    }
    raw.update(kw)
    return raw


def _run(
    run_id: int,
    *,
    conclusion: str = "success",
    attempt: int = 1,
    name: str = "CI",
    updated: str = "2026-07-15T13:00:00Z",
    **kw,
) -> dict:
    raw = {
        "id": run_id,
        "name": name,
        "conclusion": conclusion,
        "run_attempt": attempt,
        "html_url": f"https://github.com/o/r/actions/runs/{run_id}",
        "pull_requests": [],
        "repository": {"html_url": "https://github.com/o/r"},
        "updated_at": updated,
    }
    raw.update(kw)
    return raw


def test_map_issue_basic_fields():
    ev = selection.map_issue(_issue(7, login="carol"))
    assert ev is not None
    assert ev.source == "github"
    assert ev.kind == "issue_opened"
    assert ev.external_id == "7"
    assert ev.actor == "carol"
    assert ev.url == "http://gh/7"


def test_map_issue_skips_pull_requests():
    assert selection.map_issue(_issue(7, pull_request={"url": "http://pr"})) is None


def test_map_issue_none_without_number_or_date():
    assert selection.map_issue({"title": "brak numeru"}) is None
    assert selection.map_issue({"number": 1}) is None  # brak created_at


def test_map_comment_derives_issue_number_in_title():
    ev = selection.map_comment(_comment(101, issue=42))
    assert ev is not None
    assert ev.kind == "issue_comment"
    assert ev.external_id == "101"
    assert "#42" in ev.title


def test_map_issue_clips_long_body():
    ev = selection.map_issue(_issue(7, body="x" * 900))
    assert ev is not None
    assert ev.summary.endswith("[…]")
    assert len(ev.summary) < 900


def test_select_events_skips_self_and_sorts_by_time():
    issues = [_issue(1, login="me"), _issue(2, login="alice")]
    comments = [_comment(9, login="me")]
    events = selection.select_events(issues, comments, self_login="me")
    # Zdarzenia autorstwa "me" (issue 1 i komentarz 9) pominięte — zostaje tylko issue 2.
    assert [(e.kind, e.external_id) for e in events] == [("issue_opened", "2")]


def test_select_events_empty_self_login_keeps_all():
    events = selection.select_events([_issue(1, login="me")], [], self_login="")
    assert len(events) == 1  # pusty self_login wyłącza filtr (nie gubimy zdarzeń)


def test_next_since_advances_to_newest_updated():
    raws = [
        {"updated_at": "2026-07-15T10:00:00Z"},
        {"updated_at": "2026-07-15T12:00:00Z"},
        {"updated_at": "2026-07-15T11:00:00Z"},
    ]
    assert selection.next_since(raws, "2026-07-15T09:00:00Z") == "2026-07-15T12:00:00Z"
    assert selection.next_since([], "2026-07-15T09:00:00Z") == "2026-07-15T09:00:00Z"


def test_next_since_uses_submitted_at_field_for_reviews():
    raws = [
        _review(1, submitted_at="2026-07-15T10:00:00Z"),
        _review(2, submitted_at="2026-07-15T14:00:00Z"),
    ]
    # Recenzje nie mają ``updated_at`` — watermark liczymy z ``submitted_at``.
    assert selection.next_since(raws, "", field="submitted_at") == "2026-07-15T14:00:00Z"


# --- map_pull (pr_opened) ---------------------------------------------------


def test_map_pull_basic_fields():
    ev = selection.map_pull(_issue(8, login="carol", pull_request={"url": "http://pr"}))
    assert ev is not None
    assert ev.kind == "pr_opened"
    assert ev.external_id == "8"
    assert ev.actor == "carol"
    assert ev.url == "http://gh/8"


def test_map_pull_none_for_plain_issue():
    assert selection.map_pull(_issue(8)) is None  # brak klucza pull_request


def test_map_pull_none_without_number_or_date():
    assert selection.map_pull({"pull_request": {}, "title": "brak numeru"}) is None
    assert selection.map_pull({"pull_request": {}, "number": 1}) is None  # brak created_at


# --- map_comment: pr_comment vs issue_comment -------------------------------


def test_map_comment_pr_when_html_url_has_pull():
    ev = selection.map_comment(_comment(101, html_url="https://github.com/o/r/pull/9#c-101"))
    assert ev is not None
    assert ev.kind == "pr_comment"
    assert "PR" in ev.title


def test_map_comment_issue_when_html_url_lacks_pull():
    ev = selection.map_comment(_comment(101, html_url="https://github.com/o/r/issues/9#c-101"))
    assert ev is not None
    assert ev.kind == "issue_comment"


# --- map_review (pr_review) -------------------------------------------------


def test_map_review_approved_extracts_pr_number():
    ev = selection.map_review(_review(55, state="APPROVED", pr=12, login="carol"))
    assert ev is not None
    assert ev.kind == "pr_review"
    assert ev.external_id == "55"
    assert ev.actor == "carol"
    assert "#12" in ev.title


def test_map_review_changes_requested_is_event():
    ev = selection.map_review(_review(55, state="CHANGES_REQUESTED"))
    assert ev is not None
    assert ev.kind == "pr_review"


@pytest.mark.parametrize("state", ["COMMENTED", "PENDING", "DISMISSED", ""])
def test_map_review_none_for_noise_states(state):
    assert selection.map_review(_review(55, state=state)) is None


def test_map_review_none_without_id_or_date():
    assert selection.map_review({"state": "APPROVED", "submitted_at": "2026-07-15T12:00Z"}) is None
    assert selection.map_review({"id": 1, "state": "APPROVED"}) is None  # brak submitted_at


# --- map_ci_run (ci_success / ci_failure) -----------------------------------


@pytest.mark.parametrize(
    "conclusion,kind",
    [
        ("success", "ci_success"),
        ("failure", "ci_failure"),
        ("timed_out", "ci_failure"),
        ("startup_failure", "ci_failure"),
    ],
)
def test_map_ci_run_maps_decisive_conclusions(conclusion, kind):
    ev = selection.map_ci_run(_run(1, conclusion=conclusion))
    assert ev is not None
    assert ev.kind == kind


@pytest.mark.parametrize("conclusion", ["cancelled", "skipped", "neutral", "action_required"])
def test_map_ci_run_none_for_noise_conclusions(conclusion):
    assert selection.map_ci_run(_run(1, conclusion=conclusion)) is None


def test_map_ci_run_none_without_conclusion_or_date():
    assert selection.map_ci_run(_run(1, conclusion="")) is None
    assert selection.map_ci_run(_run(1, updated_at="")) is None
    assert selection.map_ci_run({"id": 1, "conclusion": "success"}) is None  # brak updated_at


def test_map_ci_run_external_id_distinguishes_reruns():
    # Ponowienie (re-run) dzieli ``id`` — ``run_attempt`` musi rozróżnić dwa zdarzenia,
    # inaczej druga porażka zniknęłaby w dedupie magazynu.
    first = selection.map_ci_run(_run(100, conclusion="failure", attempt=1))
    second = selection.map_ci_run(_run(100, conclusion="failure", attempt=2))
    assert first is not None and second is not None
    assert first.external_id == "100#1"
    assert second.external_id == "100#2"
    assert first.external_id != second.external_id


def test_map_ci_run_canonizes_url_to_pr_page():
    ev = selection.map_ci_run(
        _run(
            7,
            pull_requests=[{"number": 12}],
            repository={"html_url": "https://github.com/o/r"},
        )
    )
    assert ev is not None
    assert ev.url == "https://github.com/o/r/pull/12"
    # Link do samego przebiegu ląduje w summary (nie gubimy go), a #12 jest w tytule.
    assert "https://github.com/o/r/actions/runs/7" in ev.summary
    assert "#12" in ev.title


def test_map_ci_run_without_pr_uses_run_url_and_empty_summary():
    ev = selection.map_ci_run(_run(7, pull_requests=[]))
    assert ev is not None
    assert ev.url == "https://github.com/o/r/actions/runs/7"
    assert ev.summary == ""


def test_map_ci_run_ignores_extra_and_sensitive_fields():
    # Biała lista pól (bezpieczeństwo, ADR 0024): nadmiarowe/wrażliwe klucze NIE mogą
    # przeciekać do żadnego pola zdarzenia.
    ev = selection.map_ci_run(
        _run(
            7,
            conclusion="failure",
            logs_url="https://leak/logs",
            jobs=[{"secret": "TOP-SECRET-JOB"}],
            head_commit={"message": "leaky-commit-msg"},
            secret_token="s3cr3t-token",
        )
    )
    assert ev is not None
    blob = f"{ev.title}\n{ev.summary}\n{ev.url}\n{ev.actor}\n{ev.external_id}"
    for leaked in ("leak/logs", "TOP-SECRET-JOB", "leaky-commit-msg", "s3cr3t-token"):
        assert leaked not in blob
    assert ev.actor == ""  # CI nie ma autora-człowieka


# --- select_events + watch_kinds -------------------------------------------


def test_select_events_issues_only_skips_pull_requests():
    pr = _issue(8, login="alice", pull_request={"url": "http://pr"})
    events = selection.select_events([pr], [], self_login="me", watch_kinds=("issues",))
    assert events == []  # PR pominięty, bo nasłuchujemy tylko "issues"


def test_select_events_pulls_only_emits_pr_skips_issue():
    pr = _issue(8, login="alice", pull_request={"url": "http://pr"})
    events = selection.select_events(
        [pr, _issue(9, login="alice")], [], self_login="me", watch_kinds=("pulls",)
    )
    assert [(e.kind, e.external_id) for e in events] == [("pr_opened", "8")]


def test_select_events_ci_watermark_filters_old_runs():
    old = _run(1, updated="2026-07-15T09:00:00Z")
    fresh = _run(2, updated="2026-07-15T15:00:00Z")
    events = selection.select_events(
        [],
        [],
        raw_runs=[old, fresh],
        self_login="me",
        watch_kinds=("ci",),
        runs_since="2026-07-15T10:00:00Z",
    )
    assert [e.external_id for e in events] == ["2#1"]  # <= watermark pominięty


def test_select_events_review_watermark_filters_old_reviews():
    old = _review(1, submitted_at="2026-07-15T09:00:00Z")
    fresh = _review(2, submitted_at="2026-07-15T15:00:00Z")
    events = selection.select_events(
        [],
        [],
        raw_reviews=[old, fresh],
        self_login="me",
        watch_kinds=("reviews",),
        reviews_since="2026-07-15T10:00:00Z",
    )
    assert [e.external_id for e in events] == ["2"]


def test_select_events_review_self_skip():
    events = selection.select_events(
        [], [], raw_reviews=[_review(1, login="me")], self_login="me", watch_kinds=("reviews",)
    )
    assert events == []  # recenzja autorstwa konta bota pominięta (strażnik pętli)


def test_select_events_default_watch_kinds_backward_compatible():
    # Domyślne ("issues","comments") mapuje issue i komentarze jak przed ADR 0024.
    events = selection.select_events([_issue(1, login="alice")], [_comment(9)], self_login="me")
    assert {(e.kind, e.external_id) for e in events} == {
        ("issue_opened", "1"),
        ("issue_comment", "9"),
    }
