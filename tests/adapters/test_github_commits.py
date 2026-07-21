"""Testy ``list_commits`` klienta GitHub (ADR 0034) na ``httpx.MockTransport`` — bez sieci.

Sprawdzamy granicę: kształt zapytania (okno czasu, autor, paginacja ``Link``) i to, że surowa
odpowiedź wraca nietknięta — białą listę pól nakłada dopiero rdzeń.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from workmate.adapters.outbound.github_api import HttpxGithubClient

_API = "https://api.github.com"
_OWNER, _REPO = "BIAP-Inteligentne-Technologie", "PIWorkmate"


def _client(handler) -> HttpxGithubClient:
    return HttpxGithubClient(httpx.Client(transport=httpx.MockTransport(handler)), "PAT-secret")


def _commit(sha: str) -> dict:
    author = {"date": "2026-07-15T09:12:00Z"}
    return {"sha": sha, "commit": {"message": f"WT-1 {sha}", "author": author}}


def test_requests_the_commits_endpoint_of_the_repo() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=[])

    _client(handler).list_commits(_OWNER, _REPO)
    assert seen["path"] == f"/repos/{_OWNER}/{_REPO}/commits"


def test_passes_time_window_and_author_as_query_params() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=[])

    _client(handler).list_commits(
        _OWNER,
        _REPO,
        since=datetime(2026, 7, 13, tzinfo=timezone.utc),
        until=datetime(2026, 7, 19, 23, 59, 59, tzinfo=timezone.utc),
        author="P0w3r223",
    )
    assert seen["params"]["since"].startswith("2026-07-13")
    assert seen["params"]["until"].startswith("2026-07-19")
    assert seen["params"]["author"] == "P0w3r223"


def test_omits_optional_params_when_not_given() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=[])

    _client(handler).list_commits(_OWNER, _REPO)
    assert "since" not in seen["params"]
    assert "author" not in seen["params"]


def test_follows_link_header_pagination() -> None:
    """Paginacja jest wspólna z resztą klienta (``_get_all``) — sprawdzamy, że działa i tutaj."""
    pages = {
        "1": ([_commit("a")], f'<{_API}/repos/{_OWNER}/{_REPO}/commits?page=2>; rel="next"'),
        "2": ([_commit("b")], ""),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        body, link = pages[page]
        headers = {"Link": link} if link else {}
        return httpx.Response(200, json=body, headers=headers)

    commits = _client(handler).list_commits(_OWNER, _REPO)
    assert [item["sha"] for item in commits] == ["a", "b"]


def test_returns_raw_payload_without_filtering() -> None:
    """Klient nie mapuje — biała lista pól to zadanie rdzenia (``WorklogService._as_commits``)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_commit("abc")])

    commits = _client(handler).list_commits(_OWNER, _REPO)
    assert commits[0]["commit"]["message"] == "WT-1 abc"
