"""Testy JiraWriteService (Gate 5 / ADR 0031) — create-only, sanityzacja, strażnik projektu."""

from __future__ import annotations

import pytest

from workmate.core.application.jira import JiraWriteService
from workmate.core.errors import WriteError


class _FakeWriter:
    """Atrapa ``JiraWritePort`` — notuje wywołania, oddaje znormalizowane odpowiedzi."""

    def __init__(self, *, created: str = "2026-07-15T10:00:00.000+0200") -> None:
        self.issues: list[tuple] = []
        self.comments: list[tuple] = []
        self._created = created

    def create_issue(self, project, issue_type, summary, description, labels=()):
        self.issues.append((project, issue_type, summary, description, labels))
        key = f"{project}-1"
        return {"key": key, "url": f"https://j/browse/{key}", "created": self._created}

    def add_comment(self, issue_key, body):
        self.comments.append((issue_key, body))
        return {
            "id": "5001",
            "url": f"https://j/browse/{issue_key}?focusedCommentId=5001",
            "created": self._created,
        }


class _FakeEvents:
    """Atrapa ``EventService`` — notuje przyjęte zdarzenia echa."""

    def __init__(self) -> None:
        self.ingested: list = []

    def ingest(self, event):
        self.ingested.append(event)
        return event


def _service(writer=None, events=None, *, project="WM", issue_type="Task") -> JiraWriteService:
    return JiraWriteService(
        writer or _FakeWriter(), project=project, issue_type=issue_type, events=events
    )


# --- create_issue -----------------------------------------------------------


def test_create_issue_uses_config_project_and_type_not_request():
    writer = _FakeWriter()
    _service(writer, project="WM", issue_type="Bug").create_issue("Tytuł", "Opis")
    project, issue_type, summary, description, labels = writer.issues[0]
    assert project == "WM" and issue_type == "Bug"  # z konfiguracji, nie z treści
    assert summary == "Tytuł" and description == "Opis"


def test_create_issue_returns_key_and_url():
    result = _service().create_issue("Tytuł", "Opis")
    assert result == {"key": "WM-1", "url": "https://j/browse/WM-1"}


def test_create_issue_echoes_source_teams_event():
    events = _FakeEvents()
    _service(events=events).create_issue("Tytuł", "Opis")
    assert len(events.ingested) == 1
    ev = events.ingested[0]
    assert ev.source == "teams"  # strażnik pętli: notifier source=jira go nie odeśle
    assert ev.kind == "jira_issue_created"
    assert ev.external_id == "WM-1"


def test_create_issue_passes_labels():
    writer = _FakeWriter()
    _service(writer).create_issue("T", "O", labels=("pilne", "backend"))
    assert writer.issues[0][4] == ("pilne", "backend")


def test_create_issue_rejects_control_chars():
    with pytest.raises(WriteError):
        _service().create_issue("zła\x00treść", "opis")


def test_create_issue_bounds_summary_length():
    with pytest.raises(WriteError, match="podsumowanie"):
        _service().create_issue("x" * 300, "opis")  # limit summary = 255


# --- create_comment ---------------------------------------------------------


def test_create_comment_accepts_own_project_and_echoes():
    events = _FakeEvents()
    result = _service(events=events, project="WM").create_comment("WM-5", "treść")
    assert result == {"url": "https://j/browse/WM-5?focusedCommentId=5001"}
    # Echo ma ten sam ``kind`` co odczyt (``jira_comment``) — różni się źródłem (teams), nie typem.
    assert events.ingested[0].kind == "jira_comment"
    assert events.ingested[0].external_id == "5001"


def test_create_comment_rejects_cross_project_key():
    # Klucz Jira zawiera projekt — komentarz do OPS-1 przy skonfigurowanym WM musi być odrzucony.
    with pytest.raises(WriteError, match="spoza skonfigurowanego"):
        _service(project="WM").create_comment("OPS-1", "treść")


def test_create_comment_rejects_path_traversal_key():
    # Obejście prefiksu przez path-traversal: ``WM-1/../OPS-1`` przechodziłby test „WM", a httpx
    # znormalizowałby ścieżkę do OPS-1. Walidacja pełnego kształtu klucza to blokuje (fix H1).
    writer = _FakeWriter()
    with pytest.raises(WriteError, match="nie jest poprawnym kluczem"):
        _service(writer, project="WM").create_comment("WM-1/../OPS-1", "treść")
    assert writer.comments == []  # nic nie poszło do adaptera


@pytest.mark.parametrize("bad_key", ["WM-1/../OPS-1", "WM 1", "WM-", "WM-1x", "../WM-1", "WM-1#c"])
def test_create_comment_rejects_malformed_keys(bad_key):
    with pytest.raises(WriteError):
        _service(project="WM").create_comment(bad_key, "treść")


def test_create_comment_normalizes_key_case():
    writer = _FakeWriter()
    _service(writer, project="WM").create_comment("wm-7", "treść")
    assert writer.comments[0][0] == "WM-7"  # znormalizowany do wielkich liter


def test_create_comment_rejects_key_without_project_prefix():
    with pytest.raises(WriteError):
        _service(project="WM").create_comment("12345", "treść")  # brak prefiksu projektu


def test_create_comment_rejects_control_chars():
    with pytest.raises(WriteError):
        _service(project="WM").create_comment("WM-5", "zła\x00treść")


# --- echo edge cases --------------------------------------------------------


def test_no_events_service_still_returns_result():
    result = _service(events=None).create_issue("Tytuł", "Opis")
    assert result["key"] == "WM-1"  # brak magazynu → brak echa, ale zapis się udaje


def test_echo_skipped_when_response_has_no_created():
    events = _FakeEvents()
    writer = _FakeWriter(created="")  # odpowiedź bez znacznika czasu
    _service(writer, events=events).create_issue("Tytuł", "Opis")
    assert events.ingested == []  # bez daty nie stemplujemy echa
