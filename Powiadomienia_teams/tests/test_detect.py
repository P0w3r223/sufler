from datetime import datetime, timezone

from powiadomienia_teams.domain.models import Member, Shift
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
