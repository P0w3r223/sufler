"""Testy czystej selekcji zdarzeń GitHub (mapowanie, self-skip, watermark) — bez sieci."""
from __future__ import annotations

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
