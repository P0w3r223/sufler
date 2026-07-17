"""Testy narzędzi warstwy spajającej (ADR 0019/0021): read_recent_events + bramka zapisu GitHub.

Bramkowanie jak ``save_note``: narzędzia zapisu powstają WYŁĄCZNIE z fabryki
``build_github_write_catalog`` (przy włączonej bramce). Sprawdzamy też kopertę błędów
(``WriteError`` → ``{"error": ...}``).
"""

from __future__ import annotations

from datetime import datetime, timezone

from workmate.core.application.github import GithubWriteService
from workmate.core.application.tools import (
    build_events_catalog,
    build_github_write_catalog,
)
from workmate.core.domain.events import Event, NewEvent
from workmate.core.errors import WriteError

_WHEN = datetime(2026, 7, 15, tzinfo=timezone.utc)


class _FakeStore:
    def __init__(self) -> None:
        self.rows: list[Event] = []

    def exists(self, s, e, k):
        return False

    def append(self, event: NewEvent) -> Event:
        row = Event(id=len(self.rows) + 1, ingested_at=_WHEN, **event.model_dump())
        self.rows.append(row)
        return row

    def read_since(self, after_id, *, source=None, project=None, limit=50):
        return []

    def recent(self, *, source=None, project=None, limit=20):
        return list(reversed(self.rows))[:limit]


class _EventsService:
    """Minimalna atrapa EventService dla narzędzia read_recent_events."""

    def __init__(self, store):
        self._store = store

    def recent(self, *, source=None, project=None, limit=20):
        return self._store.recent(source=source, project=project, limit=limit)


class _OkWriter:
    def create_issue(self, owner, repo, title, body, labels):
        return {"number": 7, "html_url": "http://gh/7", "created_at": "2026-07-15T10:00:00Z"}

    def create_comment(self, owner, repo, issue_number, body):
        return {"id": 1, "html_url": "http://gh/c/1", "created_at": "2026-07-15T10:00:00Z"}


class _FailingWriter:
    def create_issue(self, *a, **k):
        raise WriteError("nie udało się utworzyć issue (HTTP 422).")

    def create_comment(self, *a, **k):
        raise WriteError("nie udało się dodać komentarza (HTTP 403).")


def _tool(catalog, name):
    return next(spec.fn for spec in catalog if spec.name == name)


def test_events_catalog_exposes_read_recent_events():
    store = _FakeStore()
    store.append(NewEvent(source="github", kind="issue_opened", external_id="1", occurred_at=_WHEN))
    catalog = build_events_catalog(_EventsService(store))
    assert [s.name for s in catalog] == ["read_recent_events"]
    result = _tool(catalog, "read_recent_events")()
    assert result["count"] == 1
    assert result["events"][0]["external_id"] == "1"


def test_write_catalog_exposes_gated_tools():
    service = GithubWriteService(_OkWriter(), owner="o", repo="r")
    catalog = build_github_write_catalog(service)
    assert {s.name for s in catalog} == {"create_github_issue", "comment_github_issue"}


def test_create_github_issue_tool_returns_created():
    service = GithubWriteService(_OkWriter(), owner="o", repo="r")
    catalog = build_github_write_catalog(service)
    result = _tool(catalog, "create_github_issue")(title="Awaria", body="opis")
    assert result == {"created": True, "number": 7, "url": "http://gh/7"}


def test_write_tool_envelopes_write_error():
    service = GithubWriteService(_FailingWriter(), owner="o", repo="r")
    catalog = build_github_write_catalog(service)
    result = _tool(catalog, "create_github_issue")(title="x", body="y")
    assert "error" in result and "422" in result["error"]  # WriteError → {"error": ...}
