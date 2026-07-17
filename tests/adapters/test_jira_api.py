"""Testy klienta Jira REST v2 (HttpxJiraClient) na ``httpx.MockTransport`` — bez sieci.

Sedno: nagłówek PAT Bearer, odczyt konta (/myself), paginacja /search po ``startAt`` (koperta
``{issues, total, ...}``) oraz przekazanie ``jql``/``expand``; zapis (ADR 0031) — payload create,
follow-up GET na ``created``, normalizacja ``{key,url,created}`` i tłumaczenie błędu HTTP→Write.
"""

from __future__ import annotations

import httpx
import pytest

from workmate.adapters.outbound.jira_api import HttpxJiraClient
from workmate.core.errors import WriteError

_BASE = "https://jira.example.com"


def _client(handler) -> HttpxJiraClient:
    transport = httpx.MockTransport(handler)
    return HttpxJiraClient(httpx.Client(transport=transport), "PAT-secret", base_url=_BASE)


def test_sets_bearer_auth_and_reads_account():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"name": "svc-bot"})

    account = _client(handler).authenticated_account()
    assert account == "svc-bot"
    assert seen["auth"] == "Bearer PAT-secret"
    assert seen["path"] == "/rest/api/2/myself"


def test_search_issues_paginates_by_start_at():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        start = int(request.url.params.get("startAt"))
        key = "WM-1" if start == 0 else "WM-2"
        return httpx.Response(
            200, json={"issues": [{"key": key}], "total": 2, "startAt": start, "maxResults": 1}
        )

    issues = _client(handler).search_issues("project=WM", max_results=1)
    assert [i["key"] for i in issues] == ["WM-1", "WM-2"]
    assert calls["n"] == 2


def test_search_issues_sends_jql_and_expand():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["jql"] = request.url.params.get("jql")
        seen["expand"] = request.url.params.get("expand")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"issues": [], "total": 0})

    _client(handler).search_issues("project=WM AND updated>='2026-07-01'")
    assert seen["path"] == "/rest/api/2/search"
    assert "project=WM" in seen["jql"]
    assert seen["expand"] == "changelog"


# --- write (ADR 0031) -------------------------------------------------------


def test_create_issue_posts_fields_and_fetches_created():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            import json

            seen["payload"] = json.loads(request.content)
            return httpx.Response(201, json={"key": "WM-9", "id": "10009"})
        # follow-up GET po ``created`` (odpowiedź create go nie niesie)
        seen["get_fields"] = request.url.params.get("fields")
        return httpx.Response(200, json={"fields": {"created": "2026-07-15T10:00:00.000+0200"}})

    result = _client(handler).create_issue("WM", "Task", "Tytuł", "Opis", ("pilne",))
    assert result["key"] == "WM-9"
    assert result["url"] == f"{_BASE}/browse/WM-9"
    assert result["created"] == "2026-07-15T10:00:00.000+0200"
    assert seen["get_fields"] == "created"
    fields = seen["payload"]["fields"]
    assert fields["project"] == {"key": "WM"}
    assert fields["issuetype"] == {"name": "Task"}
    assert fields["summary"] == "Tytuł" and fields["labels"] == ["pilne"]


def test_add_comment_posts_body_and_normalizes_url():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/2/issue/WM-5/comment"
        return httpx.Response(201, json={"id": "5001", "created": "2026-07-15T11:00:00.000+0200"})

    result = _client(handler).add_comment("WM-5", "treść komentarza")
    assert result["id"] == "5001"
    assert result["url"] == f"{_BASE}/browse/WM-5?focusedCommentId=5001"
    assert result["created"] == "2026-07-15T11:00:00.000+0200"


def test_create_issue_translates_http_error_to_write_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"errors": {"summary": "wymagane"}})

    with pytest.raises(WriteError, match="utworzyć zgłoszenia"):
        _client(handler).create_issue("WM", "Task", "", "opis")


def test_create_issue_survives_failed_created_fetch():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"key": "WM-9"})
        return httpx.Response(500)  # follow-up GET pada — issue i tak powstało

    result = _client(handler).create_issue("WM", "Task", "Tytuł", "Opis")
    assert result["key"] == "WM-9"
    assert result["created"] == ""  # best-effort: brak daty → echo zostanie pominięte
