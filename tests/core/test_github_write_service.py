"""Testy serwisu zapisu do GitHub (GithubWriteService, Gate 4 / ADR 0021) — na atrapach.

Sedno: sanityzacja treści, twardy sufit długości, create-only, miejsce zapisu z konfiguracji
(nie z treści), echo zdarzenia ``source="teams"`` do wspólnego magazynu (druga strona je widzi).
"""
from __future__ import annotations

import pytest

from workmate.core.application.github import GithubWriteService
from workmate.core.domain.events import NewEvent
from workmate.core.errors import WriteError


class _FakeWriter:
    def __init__(self) -> None:
        self.issues: list = []
        self.comments: list = []

    def create_issue(self, owner, repo, title, body, labels):
        self.issues.append((owner, repo, title, body, labels))
        return {
            "number": 12,
            "html_url": "http://gh/12",
            "created_at": "2026-07-15T10:00:00Z",
        }

    def create_comment(self, owner, repo, issue_number, body):
        self.comments.append((owner, repo, issue_number, body))
        return {
            "id": 99,
            "html_url": "http://gh/c/99",
            "created_at": "2026-07-15T11:00:00Z",
        }


class _RecordingEvents:
    def __init__(self) -> None:
        self.ingested: list[NewEvent] = []

    def ingest(self, event: NewEvent):
        self.ingested.append(event)
        return event


def _service(writer=None, events=None) -> GithubWriteService:
    return GithubWriteService(
        writer or _FakeWriter(), owner="biap", repo="workmate", events=events
    )


def test_create_issue_returns_number_and_url_from_config_repo():
    writer = _FakeWriter()
    result = _service(writer).create_issue("Awaria", "opis")
    assert result == {"number": 12, "url": "http://gh/12"}
    # Miejsce zapisu z KONFIGURACJI (biap/workmate), nie z treści prośby.
    assert writer.issues[0][:2] == ("biap", "workmate")


def test_create_issue_rejects_control_characters():
    with pytest.raises(WriteError):
        _service().create_issue("zły\x00tytuł", "opis")


def test_create_issue_rejects_too_long_title():
    with pytest.raises(WriteError, match="tytuł"):
        _service().create_issue("x" * 300, "opis")


def test_create_issue_rejects_too_long_body():
    with pytest.raises(WriteError, match="treść"):
        _service().create_issue("ok", "x" * 60_001)


def test_create_issue_echoes_teams_event():
    events = _RecordingEvents()
    _service(events=events).create_issue("Awaria API", "szczegóły")
    assert len(events.ingested) == 1
    ev = events.ingested[0]
    assert ev.source == "teams"  # NIE github → notifier nie odeśle (strażnik pętli)
    assert ev.kind == "github_issue_created"
    assert ev.external_id == "12"
    assert ev.occurred_at.year == 2026  # occurred_at z created_at odpowiedzi GitHub


def test_create_comment_echoes_and_returns_url():
    events = _RecordingEvents()
    result = _service(events=events).create_comment(5, "cześć")
    assert result == {"url": "http://gh/c/99"}
    assert events.ingested[0].kind == "github_comment_created"


def test_echo_skipped_without_events_store():
    # Brak magazynu → zapis do GitHub i tak działa (echo jest best-effort).
    assert _service(events=None).create_issue("ok", "opis")["number"] == 12
