"""Testy czystej domeny "moje zadania" Jira (ADR 0054/0056) — JQL i mapowanie, bez I/O."""

from __future__ import annotations

import pytest

from workmate.core.domain.jira_tasks import (
    JiraComment,
    build_history_jql,
    build_my_tasks_jql,
    build_search_jql,
    escape_jql_string,
    map_my_tasks,
    map_task_details,
    split_by_assignment,
)
from workmate.core.errors import InvalidRequestError


def test_build_my_tasks_jql_scopes_to_assignee_or_reported_unassigned() -> None:
    jql = build_my_tasks_jql("mikolaj@example.org")
    assert jql == (
        '(assignee = "mikolaj@example.org" OR (reporter = "mikolaj@example.org" AND assignee IS EMPTY)) '
        "AND resolution = EMPTY ORDER BY priority DESC, duedate ASC"
    )


def test_build_my_tasks_jql_strips_quotes_from_assignee() -> None:
    jql = build_my_tasks_jql('evil"OR 1=1')
    assert jql.startswith('(assignee = "evilOR 1=1"')
    assert jql.count('"') == 4  # dwa cudzysłowy w klauzuli assignee, dwa w reporter


def test_build_my_tasks_jql_rejects_empty_assignee() -> None:
    with pytest.raises(ValueError):
        build_my_tasks_jql("")


# --- escape_jql_string (wartości STEROWANE PRZEZ WOŁAJĄCEGO) ---------------------


def test_escape_jql_string_escapes_backslash_before_quote() -> None:
    assert escape_jql_string('a"b\\c') == 'a\\"b\\\\c'


# --- build_search_jql (whitelist + escaping + puste odrzucone) -------------------


def test_build_search_jql_combines_text_project_and_status() -> None:
    jql = build_search_jql(text="scada", project="WT", status_category="in_progress")
    assert jql == (
        'text ~ "scada" AND project = "WT" AND statusCategory = "In Progress" '
        "AND resolution = EMPTY ORDER BY updated DESC"
    )


def test_build_search_jql_done_status_omits_resolution_filter() -> None:
    assert "resolution = EMPTY" not in build_search_jql(status_category="done")


def test_build_search_jql_rejects_no_filters() -> None:
    with pytest.raises(InvalidRequestError):
        build_search_jql()


def test_build_search_jql_rejects_invalid_project_key() -> None:
    with pytest.raises(InvalidRequestError):
        build_search_jql(project="WT; DROP")


def test_build_search_jql_rejects_invalid_status_category() -> None:
    with pytest.raises(InvalidRequestError):
        build_search_jql(status_category="wat")


def test_build_search_jql_escapes_injection_in_text() -> None:
    jql = build_search_jql(text='a" OR "1"="1')
    assert '\\"' in jql


# --- build_history_jql (JQL + walidacja dat) --------------------------------------


def test_build_history_jql_with_date_range() -> None:
    jql = build_history_jql("712020:abc", "2026-01-01", "2026-12-31")
    assert jql == (
        'assignee = "712020:abc" AND statusCategory = "Done" AND resolved >= "2026-01-01" '
        'AND resolved <= "2026-12-31 23:59" ORDER BY resolved DESC'
    )


def test_build_history_jql_without_dates() -> None:
    assert build_history_jql("acc") == (
        'assignee = "acc" AND statusCategory = "Done" ORDER BY resolved DESC'
    )


def test_build_history_jql_strips_quotes_from_assignee() -> None:
    assert build_history_jql('a"b').startswith('assignee = "ab"')


def test_build_history_jql_rejects_malformed_since_date() -> None:
    with pytest.raises(InvalidRequestError):
        build_history_jql("acc", since="01.01.2026")


def test_build_history_jql_rejects_nonexistent_day() -> None:
    with pytest.raises(InvalidRequestError):
        build_history_jql("acc", since="2026-02-30")


def test_build_history_jql_rejects_nonexistent_month() -> None:
    with pytest.raises(InvalidRequestError):
        build_history_jql("acc", since="2026-13-01")


def test_build_history_jql_rejects_since_after_until() -> None:
    with pytest.raises(InvalidRequestError):
        build_history_jql("acc", since="2026-06-01", until="2026-01-01")


def test_build_history_jql_rejects_injection_via_date() -> None:
    with pytest.raises(InvalidRequestError):
        build_history_jql("acc", since='2026-01-01" OR project=X')


def test_build_history_jql_rejects_empty_assignee() -> None:
    with pytest.raises(ValueError):
        build_history_jql("")


# --- map_task_details (whitelist, przycięcie, strip znaków sterujących) ----------


def test_map_task_details_maps_key_and_url() -> None:
    issue = {"key": "WT-5", "fields": {"summary": "Task 2", "duedate": "2026-07-23"}}
    details = map_task_details(issue, [], base_url="https://x.atlassian.net")
    assert details.key == "WT-5"
    assert details.url.endswith("/browse/WT-5")


def test_map_task_details_maps_assignee_and_reporter_display_name() -> None:
    issue = {
        "key": "WT-5",
        "fields": {
            "summary": "Task 2",
            "assignee": {"displayName": "Piotr"},
            "reporter": {"displayName": "Piotr"},
        },
    }
    details = map_task_details(issue, [])
    assert details.assignee == "Piotr"
    assert details.reporter == "Piotr"


def test_map_task_details_strips_control_chars_from_description() -> None:
    issue = {"key": "WT-5", "fields": {"summary": "x", "description": "opis\x00 z NUL\x07"}}
    details = map_task_details(issue, [])
    assert "\x00" not in details.description
    assert "\x07" not in details.description
    assert details.description.startswith("opis")


def test_map_task_details_sanitizes_comment_body() -> None:
    issue = {"key": "WT-5", "fields": {"summary": "x"}}
    comments = [
        {"author": {"displayName": "Adam"}, "created": "2026-08-01", "body": "ping\x1b[31m"}
    ]
    details = map_task_details(issue, comments)
    assert isinstance(details.comments[0], JiraComment)
    assert "\x1b" not in details.comments[0].body


def test_map_task_details_clips_long_description() -> None:
    issue = {"key": "WT-9", "fields": {"summary": "x", "description": "a" * 5000}}
    details = map_task_details(issue, [])
    assert len(details.description) <= 2010


# --- map_my_tasks: assignee/resolved (dopracowanie "moich zadań") ----------------


def test_map_my_tasks_maps_assignee_and_resolved() -> None:
    raw = [
        {
            "key": "WT-1",
            "fields": {
                "summary": "Zrobione",
                "status": {"name": "Gotowe"},
                "assignee": {"displayName": "Piotr"},
                "resolutiondate": "2026-03-01T10:00:00.000+0100",
            },
        }
    ]
    tasks = map_my_tasks(raw)
    assert tasks[0].assignee == "Piotr"
    assert tasks[0].resolved == "2026-03-01T10:00:00.000+0100"


def test_map_my_tasks_missing_assignee_and_resolved_are_blank() -> None:
    raw = [{"key": "SCRUM-4", "fields": {"summary": "Zgłoszone", "status": {"name": "To Do"}}}]
    tasks = map_my_tasks(raw)
    assert tasks[0].assignee == ""
    assert tasks[0].resolved == ""


# --- split_by_assignment: partycja przypisane/nieprzypisane ----------------------


def test_split_by_assignment_partitions_by_empty_assignee() -> None:
    raw = [
        {"key": "WT-1", "fields": {"summary": "A", "assignee": {"displayName": "Piotr"}}},
        {"key": "SCRUM-4", "fields": {"summary": "C"}},
    ]
    assigned, unassigned = split_by_assignment(map_my_tasks(raw))
    assert [t.key for t in assigned] == ["WT-1"]
    assert [t.key for t in unassigned] == ["SCRUM-4"]


def test_map_my_tasks_maps_known_fields() -> None:
    raw = [
        {
            "key": "WM-5",
            "fields": {
                "summary": "Zrobić X",
                "status": {"name": "In Progress"},
                "priority": {"name": "High"},
                "duedate": "2026-08-01",
            },
        }
    ]
    tasks = map_my_tasks(raw, base_url="https://jira.example.org")
    assert len(tasks) == 1
    task = tasks[0]
    assert task.key == "WM-5"
    assert task.summary == "Zrobić X"
    assert task.status == "In Progress"
    assert task.priority == "High"
    assert task.due_date == "2026-08-01"
    assert task.url == "https://jira.example.org/browse/WM-5"


def test_map_my_tasks_tolerates_missing_optional_fields() -> None:
    raw = [{"key": "WM-6", "fields": {"summary": "Bez priorytetu i terminu"}}]
    tasks = map_my_tasks(raw)
    assert tasks[0].priority == ""
    assert tasks[0].due_date == ""
    assert tasks[0].url == ""


def test_map_my_tasks_skips_entries_without_key() -> None:
    raw = [{"fields": {"summary": "brak klucza"}}, {"key": "WM-7", "fields": {}}]
    tasks = map_my_tasks(raw)
    assert [t.key for t in tasks] == ["WM-7"]


def test_map_my_tasks_clips_long_summary() -> None:
    raw = [{"key": "WM-8", "fields": {"summary": "x" * 500}}]
    tasks = map_my_tasks(raw)
    assert tasks[0].summary.endswith("[…]")
    assert len(tasks[0].summary) < 500


def test_map_my_tasks_empty_input_returns_empty_list() -> None:
    assert map_my_tasks([]) == []
