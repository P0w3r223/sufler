import json
import logging
from datetime import datetime, timezone

import httpx
import pytest

from powiadomienia_teams.graph.auth import AuthExpiredError
from powiadomienia_teams.graph.client import GraphClient, GraphPermissionError

UTC = timezone.utc


def _graph(handler) -> GraphClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    gc = GraphClient(http, lambda: "tok", sleep=lambda _s: None)
    gc.refresh_auth()
    return gc


def test_list_members_maps_and_paginates():
    pages = {
        "https://graph.microsoft.com/v1.0/teams/T/members": {
            "value": [{"userId": "u1", "displayName": "Ala", "roles": ["owner"]}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/teams/T/members?page=2",
        },
        "https://graph.microsoft.com/v1.0/teams/T/members?page=2": {
            "value": [{"userId": "u2", "displayName": "Bok"}],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json=pages[str(request.url)])

    members = _graph(handler).list_members("T")
    assert [m.user_id for m in members] == ["u1", "u2"]


def test_list_members_skips_entries_without_userid():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"value": [{"userId": "u1", "displayName": "Ala"}, {"displayName": "Bot"}]},
        )

    members = _graph(handler).list_members("T")
    assert [m.user_id for m in members] == ["u1"]


def test_read_shifts_filters_window():
    body = {
        "value": [
            {
                "userId": "u1",
                "sharedShift": {
                    "startDateTime": "2026-07-20T08:00:00Z",
                    "endDateTime": "2026-07-20T16:00:00Z",
                },
            },
            {  # poza oknem — pomijane
                "userId": "u1",
                "sharedShift": {
                    "startDateTime": "2026-07-10T08:00:00Z",
                    "endDateTime": "2026-07-10T16:00:00Z",
                },
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    out = _graph(handler).read_shifts(
        "T", datetime(2026, 7, 20, tzinfo=UTC), datetime(2026, 7, 27, tzinfo=UTC)
    )
    assert len(out) == 1
    assert out[0].start == datetime(2026, 7, 20, 8, tzinfo=UTC)


def test_get_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json={"id": "me-id"})

    assert _graph(handler).get_me() == "me-id"
    assert calls["n"] == 2


def test_create_or_get_chat_posts_oneonone_and_returns_id():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://graph.microsoft.com/v1.0/chats"
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "chat-123"})

    chat_id = _graph(handler).create_or_get_chat("me", "u1")
    assert chat_id == "chat-123"
    assert seen["body"]["chatType"] == "oneOnOne"
    assert len(seen["body"]["members"]) == 2


def test_send_chat_message_posts_html():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://graph.microsoft.com/v1.0/chats/chat-123/messages"
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "msg-1"})

    _graph(handler).send_chat_message("chat-123", "<p>hej</p>")
    assert seen["body"]["body"]["contentType"] == "html"
    assert seen["body"]["body"]["content"] == "<p>hej</p>"


def test_list_chat_messages_returns_value():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "chats/chat-1/messages" in str(request.url)
        return httpx.Response(200, json={"value": [{"id": "m1"}, {"id": "m2"}]})

    msgs = _graph(handler).list_chat_messages("chat-1")
    assert [m["id"] for m in msgs] == ["m1", "m2"]


def test_create_shift_posts_shared_shift():
    from powiadomienia_teams.domain.models import Shift

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://graph.microsoft.com/v1.0/teams/T/schedule/shifts"
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "shift-1"})

    shift = Shift(
        "u1",
        datetime(2026, 7, 20, 6, tzinfo=UTC),
        datetime(2026, 7, 20, 14, tzinfo=UTC),
        scheduling_group_id="TAG",
    )
    shift_id = _graph(handler).create_shift("T", shift)
    assert shift_id == "shift-1"
    assert seen["body"]["userId"] == "u1"
    assert seen["body"]["schedulingGroupId"] == "TAG"
    assert seen["body"]["sharedShift"]["startDateTime"] == "2026-07-20T06:00:00Z"
    assert seen["body"]["sharedShift"]["theme"] == "green"  # brak koloru → domyślnie stacjonarnie


def test_create_shift_includes_theme_when_set():
    from powiadomienia_teams.domain.models import Shift

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "shift-2"})

    shift = Shift(
        "u1",
        datetime(2026, 7, 20, 6, tzinfo=UTC),
        datetime(2026, 7, 20, 14, tzinfo=UTC),
        scheduling_group_id="TAG",
        theme="green",
    )
    _graph(handler).create_shift("T", shift)
    assert seen["body"]["sharedShift"]["theme"] == "green"


def test_share_schedule_posts_range():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://graph.microsoft.com/v1.0/teams/T/schedule/share"
        seen["body"] = json.loads(request.content)
        return httpx.Response(204)

    _graph(handler).share_schedule(
        "T", datetime(2026, 7, 20, tzinfo=UTC), datetime(2026, 7, 27, tzinfo=UTC)
    )
    assert seen["body"]["notifyTeam"] is True
    assert seen["body"]["startDateTime"] == "2026-07-20T00:00:00Z"


def test_get_raises_auth_expired_on_401():
    """401 mimo udanego cichego odświeżenia = token odrzucony przez Graph — usługa ma stanąć."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})

    with pytest.raises(AuthExpiredError, match="--login"):
        _graph(handler).list_members("T")


def test_post_raises_auth_expired_on_401():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})

    with pytest.raises(AuthExpiredError):
        _graph(handler).send_chat_message("chat-1", "<p>x</p>")


def test_403_raises_permission_error_with_body():
    """Ciało 403 to jedyne miejsce z przyczyną — musi trafić i do wyjątku, i do logu."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": "Forbidden",
                    "message": "Missing scope Schedule.ReadWrite.All",
                }
            },
        )

    with pytest.raises(GraphPermissionError, match="Schedule.ReadWrite.All"):
        _graph(handler).list_members("T")


def test_error_body_is_logged(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal boom")

    with caplog.at_level(logging.ERROR), pytest.raises(httpx.HTTPStatusError):
        _graph(handler).list_members("T")
    assert "Internal boom" in caplog.text


def test_5xx_still_raises_http_status_error():
    """Transientne 5xx zostaje zwykłym błędem HTTP — ponawianie wyżej ma sens."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="try later")

    with pytest.raises(httpx.HTTPStatusError):
        _graph(handler).list_members("T")


def test_read_time_off_uses_overlap_not_start():
    """Urlop zaczęty PRZED oknem i trwający w nim musi być widoczny — inaczej osoba w środku
    dwutygodniowego urlopu wyszłaby jako »bez grafiku« i dostałaby prośbę."""
    body = {
        "value": [
            {  # zaczyna się tydzień wcześniej, ale przecina okno docelowe
                "userId": "u1",
                "sharedTimeOff": {
                    "startDateTime": "2026-07-13T00:00:00Z",
                    "endDateTime": "2026-07-25T00:00:00Z",
                    "timeOffReasonId": "TOR_URLOP",
                },
            },
            {  # w całości po oknie — pomijany
                "userId": "u2",
                "sharedTimeOff": {
                    "startDateTime": "2026-08-01T00:00:00Z",
                    "endDateTime": "2026-08-05T00:00:00Z",
                    "timeOffReasonId": "TOR_URLOP",
                },
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/schedule/timesOff")
        return httpx.Response(200, json=body)

    out = _graph(handler).read_time_off(
        "T", datetime(2026, 7, 20, tzinfo=UTC), datetime(2026, 7, 27, tzinfo=UTC)
    )
    assert [t.user_id for t in out] == ["u1"]


def _graph_z_zapisem_snu(handler, spane: list):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    gc = GraphClient(http, lambda: "tok", sleep=spane.append)
    gc.refresh_auth()
    return gc


def test_dlugi_retry_after_jest_honorowany_w_ramach_budzetu():
    """Ograniczamy SUMĘ czekania, nie pojedynczą przerwę.

    Sufit 60 s na próbę wyglądał ostrożnie, ale zamieniał „wolno, ale w końcu się uda"
    w „porzucone": przy `Retry-After: 3600` pięć prób wyczerpywało się w pięć minut i przebieg
    tygodniowy padał. Ignorowanie nagłówka bywa też karane wydłużeniem dławienia.
    """
    spane: list = []
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "3600"}, json={})
        return httpx.Response(200, json={"id": "me-id"})

    assert _graph_z_zapisem_snu(handler, spane).get_me() == "me-id"
    assert spane == [900], spane  # cały budżet w jednym oczekiwaniu, nie 60 s


def test_budzet_ponowien_nie_jest_nieskonczony():
    """Dławienie bez końca musi w końcu ustąpić błędem, a nie usypiać procesu na zawsze."""
    spane: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "300"}, json={})

    with pytest.raises(httpx.HTTPStatusError):
        _graph_z_zapisem_snu(handler, spane).get_me()
    assert sum(spane) <= 900  # łączne czekanie mieści się w budżecie
