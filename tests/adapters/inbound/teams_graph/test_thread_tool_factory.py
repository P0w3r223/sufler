"""Testy fabryki ``reply_on_thread`` per turę (_make_thread_tool_factory, ADR 0024, Faza 3b).

Fabryka mapuje ``external_id`` drzwi teams_graph (konwencja ``team/channel/root``) na cel wątku z
``ThreadLinkStore`` i wstrzykuje scoped narzędzie z PRE-ZWIĄZANYM numerem. Sprawdzamy: powiązany
wątek → narzędzie komentujące właściwy numer; brak powiązania → pusta lista; źle uformowany
``external_id`` (nie 3 części) → pusta lista bez wyjątku. Na atrapach (link store + port zapisu).
"""

from __future__ import annotations

import pytest

from workmate.adapters.inbound.teams_graph.app import (
    _build_bridge_catalog,
    _make_thread_tool_factory,
)
from workmate.config import EventsSettings, GithubSettings, JiraSettings
from workmate.core.application.github import GithubWriteService


class _RecordingWriter:
    """Atrapa ``GithubWritePort`` — notuje ``issue_number`` z create_comment."""

    def __init__(self) -> None:
        self.comments: list[tuple[str, str, int, str]] = []

    def create_issue(self, owner, repo, title, body, labels):
        return {"number": 1, "html_url": "http://gh/1", "created_at": "2026-07-15T10:00:00Z"}

    def create_comment(self, owner, repo, issue_number, body):
        self.comments.append((owner, repo, issue_number, body))
        return {"id": 5, "html_url": "http://gh/c/5", "created_at": "2026-07-15T10:00:00Z"}


class _FakeThreadLinks:
    """Atrapa ``ThreadLinkStore`` — mapuje (team, channel, root) → cel (kind, number)."""

    def __init__(self, targets: dict[tuple[str, str, str], tuple[str, str]]) -> None:
        self._targets = targets

    def get_root(self, team_id, channel_id, target_kind, target_number):
        return None

    def get_target(self, team_id, channel_id, root_id):
        return self._targets.get((team_id, channel_id, root_id))

    def link(self, team_id, channel_id, target_kind, target_number, root_id):
        return None


def _write_service(writer) -> GithubWriteService:
    return GithubWriteService(writer, owner="o", repo="r")


def test_linked_thread_yields_reply_tool_that_comments_mapped_number():
    writer = _RecordingWriter()
    links = _FakeThreadLinks({("team-1", "chan-1", "root-9"): ("pr", "12")})
    factory = _make_thread_tool_factory(links, _write_service(writer))

    tools = factory("team-1/chan-1/root-9")

    assert [spec.name for spec in tools] == ["reply_on_thread"]
    tools[0].fn(body="odpowiedź z wątku")
    # Numer wzięty z zaufanego mapowania (12), nie od modelu.
    assert writer.comments == [("o", "r", 12, "odpowiedź z wątku")]


def test_unlinked_thread_yields_empty_list():
    links = _FakeThreadLinks({})  # get_target zwróci None
    factory = _make_thread_tool_factory(links, _write_service(_RecordingWriter()))

    assert factory("team-1/chan-1/root-9") == []


def test_malformed_external_id_without_three_parts_yields_empty_list():
    """Sam nadawca (``u1``) zamiast ``team/channel/root`` → pusta lista, bez wyjątku."""
    links = _FakeThreadLinks({})
    factory = _make_thread_tool_factory(links, _write_service(_RecordingWriter()))

    assert factory("u1") == []


def test_too_many_parts_external_id_yields_empty_list():
    links = _FakeThreadLinks({("a", "b", "c"): ("issue", "1")})
    factory = _make_thread_tool_factory(links, _write_service(_RecordingWriter()))

    # 4 części ≠ 3 → nie próbujemy mapować (obrona przed nietypowym id).
    assert factory("a/b/c/d") == []


def test_bridge_catalog_gate_off_yields_no_thread_factory():
    """Strukturalna gwarancja: zapis do GitHub OFF → fabryka wątkowa NIE powstaje (None).

    Bez włączonej bramki agent nie dostaje ani narzędzi zapisu, ani ``reply_on_thread`` —
    powierzchnia mutująca w ogóle się nie materializuje (jak Gate 4 / ADR 0021).
    """
    catalog, factory = _build_bridge_catalog(
        EventsSettings(db_path=":memory:"),
        GithubSettings(enable_github_write=False, token="t", owner="o", repo="r"),
        JiraSettings(enable_jira_write=False),
    )

    assert factory is None  # brak fabryki = brak reply_on_thread
    # Odczyt zdarzeń + podsumowanie aktywności projektu (ADR 0029); zapis GitHub/Jira OFF.
    assert [spec.name for spec in catalog] == ["read_recent_events", "get_project_activity"]


def test_bridge_catalog_jira_write_on_adds_jira_tools():
    """Bramka zapisu Jira ON (+ token/URL/projekt) → agent dostaje narzędzia zapisu (ADR 0031)."""
    catalog, factory = _build_bridge_catalog(
        EventsSettings(db_path=":memory:"),
        GithubSettings(enable_github_write=False),
        JiraSettings(
            enable_jira_write=True,
            token="t",
            base_url="https://jira.example.com",
            write_project="WM",
        ),
    )

    names = [spec.name for spec in catalog]
    assert "create_jira_issue" in names and "comment_jira_issue" in names
    assert factory is None  # zapis Jira nie tworzy fabryki wątkowej (wątki kanału Jiry OFF w B1)


def test_bridge_catalog_jira_write_gate_off_adds_no_jira_tools():
    """Strukturalna gwarancja: zapis Jira OFF → brak narzędzi mutujących Jiry (jak Gate 4/5)."""
    catalog, _ = _build_bridge_catalog(
        EventsSettings(db_path=":memory:"),
        GithubSettings(enable_github_write=False),
        JiraSettings(enable_jira_write=False, token="t", base_url="x", write_project="WM"),
    )

    names = [spec.name for spec in catalog]
    assert "create_jira_issue" not in names and "comment_jira_issue" not in names


def test_bridge_catalog_jira_write_on_without_project_fails_fast():
    """Bramka ON, ale brak projektu docelowego → TWARDY błąd, nie cicha martwa bramka (fix M1)."""
    with pytest.raises(ValueError, match="WORKMATE_JIRA_WRITE_PROJECT"):
        _build_bridge_catalog(
            EventsSettings(db_path=":memory:"),
            GithubSettings(enable_github_write=False),
            JiraSettings(enable_jira_write=True, token="t", base_url="https://j"),
        )
