"""Testy klienta grafiku Teams Shifts (HttpxGraphScheduleClient, ADR 0056) na
``httpx.MockTransport`` — bez sieci i bez MSAL (token_provider jest wstrzykiwaną funkcją).
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone

import httpx
import pytest

from workmate.adapters.outbound.graph_schedule_api import HttpxGraphScheduleClient
from workmate.core.errors import ScheduleReadError

_START = datetime(2026, 8, 3, tzinfo=timezone.utc)
_END = datetime(2026, 8, 10, tzinfo=timezone.utc)


def _client(handler, token: str = "tok-123") -> HttpxGraphScheduleClient:
    transport = httpx.MockTransport(handler)
    return HttpxGraphScheduleClient(httpx.Client(transport=transport), lambda: token)


def test_list_members_sends_bearer_token_from_provider():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"value": [{"userId": "U1", "displayName": "Adam"}]})

    members = _client(handler, token="secret-token").list_members("team-1")
    assert members == [{"userId": "U1", "displayName": "Adam"}]
    assert seen["auth"] == "Bearer secret-token"
    assert seen["path"] == "/v1.0/teams/team-1/members"


def test_list_members_paginates_via_odata_next_link():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                200,
                json={
                    "value": [{"userId": "U1", "displayName": "Adam"}],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/teams/team-1/members?p=2",
                },
            )
        return httpx.Response(200, json={"value": [{"userId": "U2", "displayName": "Basia"}]})

    members = _client(handler).list_members("team-1")
    assert [m["userId"] for m in members] == ["U1", "U2"]
    assert calls["n"] == 2


def test_list_shifts_sends_ge_le_filter_on_shared_shift():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = urllib.parse.unquote(str(request.url))
        return httpx.Response(200, json={"value": []})

    _client(handler).list_shifts("team-1", _START, _END)
    assert "sharedShift/startDateTime ge" in seen["query"]
    assert "sharedShift/endDateTime le" in seen["query"]


def test_list_times_off_has_no_server_side_filter():
    """Nieobecność KOŃCZĄCA SIĘ po oknie musi wrócić — filtr overlap robi domena, nie Graph."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = str(request.url)
        return httpx.Response(200, json={"value": []})

    _client(handler).list_times_off("team-1", _START, _END)
    assert "$filter" not in seen["query"]


def test_list_time_off_reasons_maps_id_to_display_name():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [{"id": "R1", "displayName": "Urlop"}]})

    reasons = _client(handler).list_time_off_reasons("team-1")
    assert reasons == {"R1": "Urlop"}


@pytest.mark.parametrize("status_code", [401, 403])
def test_forbidden_response_raises_readable_schedule_read_error(status_code: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={})

    with pytest.raises(ScheduleReadError, match="Schedule.Read.All|zgody"):
        _client(handler).list_members("team-1")


def test_not_found_response_raises_readable_schedule_read_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    with pytest.raises(ScheduleReadError, match="nie znaleziono grafiku"):
        _client(handler).list_members("team-1")
