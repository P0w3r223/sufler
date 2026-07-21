"""Testy ewidencji czasu w kliencie Jira Cloud (ADR 0034) na ``httpx.MockTransport`` — bez sieci.

Najważniejsza asercja modułu: żądanie NIE zawiera pola ``author``. Jira Cloud i tak je ignoruje,
więc wysyłanie go udawałoby zdolność, której API nie ma — a stąd już blisko do cichej obietnicy
atrybucji w reszcie systemu.
"""

from __future__ import annotations

import json

import httpx
import pytest

from workmate.adapters.outbound.jira_cloud_api import HttpxJiraCloudClient
from workmate.core.domain.adf import adf_to_text
from workmate.core.errors import WriteError

_BASE = "https://example.atlassian.net"
_PIOTR = "712020:c0ffee00-0000-4000-8000-000000000008"
_MIKOLAJ = "712020:c0ffee00-0000-4000-8000-000000000018"
_STARTED = "2026-07-19T12:00:00.000+0200"


def _client(handler) -> HttpxJiraCloudClient:
    return HttpxJiraCloudClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
        email="piotr@example.com",
        token="api-token",
        base_url=_BASE,
    )


def _added(seen: dict, *, response: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            201,
            json=response
            or {
                "id": "10501",
                "created": "2026-07-20T10:00:00.000+0200",
                "timeSpentSeconds": 7200,
                "author": {"accountId": _PIOTR, "displayName": "Piotr"},
            },
        )

    return handler


def test_posts_to_the_worklog_endpoint() -> None:
    seen: dict = {}
    _client(_added(seen)).add_worklog("WT-12", time_spent_seconds=7200, started=_STARTED)
    assert seen["method"] == "POST"
    assert seen["path"] == "/rest/api/3/issue/WT-12/worklog"


def test_request_body_carries_seconds_and_started() -> None:
    seen: dict = {}
    _client(_added(seen)).add_worklog("WT-12", time_spent_seconds=5400, started=_STARTED)
    assert seen["body"]["timeSpentSeconds"] == 5400
    assert seen["body"]["started"] == _STARTED


def test_request_body_never_carries_an_author_field() -> None:
    """Rdzeń szkieletu: Cloud ignoruje ``author``, więc go NIE wysyłamy nawet cross-user."""
    seen: dict = {}
    _client(_added(seen)).add_worklog(
        "WT-12", time_spent_seconds=7200, started=_STARTED, on_behalf_of=_MIKOLAJ
    )
    assert "author" not in seen["body"]
    assert "authorAccountId" not in seen["body"]


def test_comment_is_encoded_as_adf() -> None:
    seen: dict = {}
    _client(_added(seen)).add_worklog(
        "WT-12", time_spent_seconds=7200, started=_STARTED, comment="w imieniu: Mikołaj"
    )
    assert adf_to_text(seen["body"]["comment"]) == "w imieniu: Mikołaj"


def test_empty_comment_is_omitted_entirely() -> None:
    seen: dict = {}
    _client(_added(seen)).add_worklog("WT-12", time_spent_seconds=7200, started=_STARTED)
    assert "comment" not in seen["body"]


def test_response_reports_real_author_and_echoes_intent() -> None:
    """``author_account_id`` to PRAWDA z Jiry, ``requested_author`` to intencja wołającego."""
    result = _client(_added({})).add_worklog(
        "WT-12", time_spent_seconds=7200, started=_STARTED, on_behalf_of=_MIKOLAJ
    )
    assert result["author_account_id"] == _PIOTR
    assert result["requested_author"] == _MIKOLAJ
    assert result["id"] == "10501"
    assert result["url"] == f"{_BASE}/browse/WT-12"


@pytest.mark.parametrize("status", [400, 403, 404])
def test_write_errors_become_domain_write_error(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"errorMessages": ["nie wolno"]})

    with pytest.raises(WriteError, match="wpisu czasu"):
        _client(handler).add_worklog("WT-12", time_spent_seconds=7200, started=_STARTED)


def test_read_worklogs_maps_entries_for_the_duplicate_guard() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/issue/WT-12/worklog"
        return httpx.Response(
            200,
            json={
                "worklogs": [
                    {
                        "id": "1",
                        "author": {"accountId": _PIOTR},
                        "started": _STARTED,
                        "timeSpentSeconds": 3600,
                        "comment": {
                            "type": "doc",
                            "version": 1,
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "przegląd"}],
                                }
                            ],
                        },
                    },
                    "śmieć nie-dict",
                ]
            },
        )

    entries = _client(handler).read_worklogs("WT-12")
    assert len(entries) == 1  # niepoprawny wpis odfiltrowany, nie wywraca odczytu
    assert entries[0]["author_account_id"] == _PIOTR
    assert entries[0]["comment"] == "przegląd"  # ADF spłaszczone NA GRANICY adaptera


def test_read_worklogs_on_missing_key_returns_empty_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    assert _client(handler).read_worklogs("WT-12") == []
