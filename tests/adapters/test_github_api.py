"""Testy klienta GitHub REST (HttpxGithubClient) na ``httpx.MockTransport`` — bez sieci.

Sedno: nagłówki uwierzytelnienia/wersji, paginacja po nagłówku ``Link`` (rel="next"),
backoff na wyczerpanym limicie (403 + Retry-After), odczyt loginu oraz zapis (issue/komentarz).
"""
from __future__ import annotations

import httpx

from workmate.adapters.outbound.github_api import HttpxGithubClient

_BASE = "https://api.github.com"


def _client(handler) -> HttpxGithubClient:
    transport = httpx.MockTransport(handler)
    return HttpxGithubClient(httpx.Client(transport=transport), "PAT-secret", api_base=_BASE)


def test_sets_auth_and_version_headers_and_reads_login():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["accept"] = request.headers.get("Accept")
        seen["version"] = request.headers.get("X-GitHub-Api-Version")
        return httpx.Response(200, json={"login": "octocat"})

    login = _client(handler).authenticated_login()
    assert login == "octocat"
    assert seen["auth"] == "Bearer PAT-secret"
    assert seen["accept"] == "application/vnd.github+json"
    assert seen["version"] == "2022-11-28"


def test_list_issues_follows_link_pagination():
    def handler(request: httpx.Request) -> httpx.Response:
        if "page=2" in str(request.url):
            return httpx.Response(200, json=[{"number": 2}])
        headers = {"Link": f'<{_BASE}/repos/o/r/issues?page=2>; rel="next"'}
        return httpx.Response(200, json=[{"number": 1}], headers=headers)

    issues = _client(handler).list_issues("o", "r")
    assert [i["number"] for i in issues] == [1, 2]


def test_list_issues_sends_since_param():
    from datetime import datetime, timezone

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["since"] = request.url.params.get("since")
        return httpx.Response(200, json=[])

    _client(handler).list_issues(
        "o", "r", since=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
    )
    assert seen["since"] == "2026-07-15T10:00:00Z"


def test_retries_on_rate_limit_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            # 403 z sygnałem wyczerpanego limitu + Retry-After: 0 (bez realnego snu w teście).
            return httpx.Response(
                403, json=[], headers={"X-RateLimit-Remaining": "0", "Retry-After": "0"}
            )
        return httpx.Response(200, json=[{"number": 7}])

    issues = _client(handler).list_issues("o", "r")
    assert calls["n"] == 2
    assert [i["number"] for i in issues] == [7]


def test_create_issue_posts_payload():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = _json.loads(request.content)
        return httpx.Response(201, json={"number": 12, "html_url": "http://gh/12"})

    result = _client(handler).create_issue("o", "r", "Tytuł", "Treść", labels=("bug",))
    assert seen["method"] == "POST"
    assert seen["path"] == "/repos/o/r/issues"
    assert seen["body"] == {"title": "Tytuł", "body": "Treść", "labels": ["bug"]}
    assert result["number"] == 12


def test_create_comment_posts_to_issue():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(201, json={"html_url": "http://gh/c/1"})

    result = _client(handler).create_comment("o", "r", 42, "cześć")
    assert seen["path"] == "/repos/o/r/issues/42/comments"
    assert result["html_url"] == "http://gh/c/1"
