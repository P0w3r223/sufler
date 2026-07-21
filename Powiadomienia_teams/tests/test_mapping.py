from datetime import datetime, timezone

from powiadomienia_teams.graph.mapping import (
    member_from_json,
    parse_graph_datetime,
    shift_from_json,
    time_off_from_json,
)

UTC = timezone.utc


def test_parse_graph_datetime_z_suffix():
    assert parse_graph_datetime("2026-07-20T08:00:00Z") == datetime(2026, 7, 20, 8, tzinfo=UTC)


def test_parse_graph_datetime_trims_seven_digit_fraction():
    dt = parse_graph_datetime("2026-07-20T08:00:00.1234567Z")
    assert dt.tzinfo is not None
    assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 7, 20, 8)


def test_member_from_json_full():
    m = member_from_json(
        {"userId": "u1", "displayName": "Ala", "email": "ala@x.pl", "roles": ["owner"]}
    )
    assert m is not None
    assert m.user_id == "u1"
    assert m.email == "ala@x.pl"
    assert m.roles == ("owner",)


def test_member_from_json_null_email():
    m = member_from_json({"userId": "u1", "displayName": "Ala", "email": None, "roles": []})
    assert m is not None
    assert m.email is None
    assert m.roles == ()


def test_member_from_json_missing_userid_is_none():
    assert member_from_json({"displayName": "Brak Id"}) is None


def test_shift_from_shared():
    raw = {
        "userId": "u1",
        "schedulingGroupId": "TAG",
        "sharedShift": {
            "startDateTime": "2026-07-20T08:00:00Z",
            "endDateTime": "2026-07-20T16:00:00Z",
            "theme": "green",
        },
    }
    s = shift_from_json(raw)
    assert s is not None
    assert s.user_id == "u1"
    assert s.scheduling_group_id == "TAG"
    assert s.theme == "green"
    assert s.start == datetime(2026, 7, 20, 8, tzinfo=UTC)


def test_shift_falls_back_to_draft():
    raw = {
        "userId": "u1",
        "draftShift": {
            "startDateTime": "2026-07-20T08:00:00Z",
            "endDateTime": "2026-07-20T16:00:00Z",
        },
    }
    s = shift_from_json(raw)
    assert s is not None
    assert s.start == datetime(2026, 7, 20, 8, tzinfo=UTC)


def test_shift_missing_body_is_none():
    assert shift_from_json({"userId": "u1"}) is None


def test_shift_missing_userid_is_none():
    raw = {
        "sharedShift": {
            "startDateTime": "2026-07-20T08:00:00Z",
            "endDateTime": "2026-07-20T16:00:00Z",
        }
    }
    assert shift_from_json(raw) is None


def test_shift_invalid_range_is_none():
    raw = {
        "userId": "u1",
        "sharedShift": {
            "startDateTime": "2026-07-20T16:00:00Z",
            "endDateTime": "2026-07-20T08:00:00Z",
        },
    }
    assert shift_from_json(raw) is None


def test_time_off_from_json_maps_shared():
    raw = {
        "userId": "u1",
        "sharedTimeOff": {
            "startDateTime": "2026-07-20T00:00:00Z",
            "endDateTime": "2026-07-25T00:00:00Z",
            "timeOffReasonId": "TOR_URLOP",
        },
    }
    out = time_off_from_json(raw)
    assert out is not None
    assert out.user_id == "u1"
    assert out.reason_id == "TOR_URLOP"


def test_time_off_from_json_falls_back_to_draft():
    raw = {
        "userId": "u1",
        "draftTimeOff": {
            "startDateTime": "2026-07-20T00:00:00Z",
            "endDateTime": "2026-07-21T00:00:00Z",
            "timeOffReasonId": "TOR_URLOP",
        },
    }
    out = time_off_from_json(raw)
    assert out is not None and out.reason_id == "TOR_URLOP"


def test_time_off_from_json_returns_none_without_reason():
    """Bez powodu wpis jest niekompletny — TimeOff wymaga reason_id, więc pomijamy go cicho."""
    raw = {
        "userId": "u1",
        "sharedTimeOff": {
            "startDateTime": "2026-07-20T00:00:00Z",
            "endDateTime": "2026-07-21T00:00:00Z",
        },
    }
    assert time_off_from_json(raw) is None


def test_time_off_from_json_returns_none_on_bad_range():
    raw = {
        "userId": "u1",
        "sharedTimeOff": {
            "startDateTime": "2026-07-25T00:00:00Z",
            "endDateTime": "2026-07-20T00:00:00Z",
            "timeOffReasonId": "TOR_URLOP",
        },
    }
    assert time_off_from_json(raw) is None
