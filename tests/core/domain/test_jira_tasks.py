"""Testy czystej domeny "moje zadania" Jira (ADR 0054) — JQL i mapowanie, bez I/O."""

from __future__ import annotations

import pytest

from workmate.core.domain.jira_tasks import build_my_tasks_jql, map_my_tasks


def test_build_my_tasks_jql_scopes_to_assignee() -> None:
    jql = build_my_tasks_jql("mikolaj@example.org")
    assert jql == (
        'assignee = "mikolaj@example.org" AND resolution = EMPTY ORDER BY priority DESC, duedate ASC'
    )


def test_build_my_tasks_jql_strips_quotes_from_assignee() -> None:
    jql = build_my_tasks_jql('evil"OR 1=1')
    assert jql.startswith('assignee = "evilOR 1=1"')
    assert jql.count('"') == 2


def test_build_my_tasks_jql_rejects_empty_assignee() -> None:
    with pytest.raises(ValueError):
        build_my_tasks_jql("")


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
