from datetime import datetime, timezone

from powiadomienia_teams.domain.models import Member, Shift, TimeOff
from powiadomienia_teams.reminders.detect import members_without_shifts

UTC = timezone.utc


def _shift(user_id: str) -> Shift:
    return Shift(
        user_id,
        datetime(2026, 7, 20, 8, tzinfo=UTC),
        datetime(2026, 7, 20, 16, tzinfo=UTC),
    )


def test_detects_only_members_without_shifts():
    members = [Member("u1", "A"), Member("u2", "B"), Member("u3", "C")]
    out = members_without_shifts(members, [_shift("u2")])
    assert [m.user_id for m in out] == ["u1", "u3"]


def test_no_shifts_means_everyone_missing():
    members = [Member("u1", "A"), Member("u2", "B")]
    assert members_without_shifts(members, []) == members


def test_all_covered_means_nobody_missing():
    members = [Member("u1", "A")]
    assert members_without_shifts(members, [_shift("u1")]) == []


def test_extra_shifts_for_unknown_users_ignored():
    members = [Member("u1", "A")]
    out = members_without_shifts(members, [_shift("u1"), _shift("ghost")])
    assert out == []


def _time_off(user_id: str, *, day: int = 20, days: int = 1) -> TimeOff:
    return TimeOff(
        user_id,
        datetime(2026, 7, day, tzinfo=UTC),
        datetime(2026, 7, day + days, tzinfo=UTC),
        reason_id="TOR_URLOP",
    )


def test_member_with_time_off_is_not_missing():
    """Zatwierdzony urlop = grafik uzupełniony; prośba byłaby nagabywaniem i dublowałaby timeOff."""
    members = [Member("u1", "A"), Member("u2", "B")]
    out = members_without_shifts(members, [], [_time_off("u2")])
    assert [m.user_id for m in out] == ["u1"]


def test_shifts_and_time_off_cover_jointly():
    members = [Member("u1", "A"), Member("u2", "B"), Member("u3", "C")]
    out = members_without_shifts(members, [_shift("u1")], [_time_off("u2")])
    assert [m.user_id for m in out] == ["u3"]
