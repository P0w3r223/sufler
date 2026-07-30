"""Testy klienta Jira REST v2 (HttpxJiraClient) na ``httpx.MockTransport`` — bez sieci.

Sedno: nagłówek PAT Bearer, odczyt konta (/myself), paginacja /search po ``startAt`` (koperta
``{issues, total, ...}``) oraz przekazanie ``jql``/``expand``. Zapis/tranzycja (ADR 0031/0032)
zostały USUNIĘTE razem z mostem (ADR 0054) — klient jest dziś wyłącznie odczytowy.
"""

from __future__ import annotations

import httpx

from workmate.adapters.outbound.jira_api import HttpxJiraClient

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
