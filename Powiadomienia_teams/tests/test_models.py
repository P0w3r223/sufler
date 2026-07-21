from datetime import date, datetime, timezone

import pytest

from powiadomienia_teams.domain.models import InvalidShift, Member, Shift, WeekSchedule

UTC = timezone.utc


def _dt(hour: int) -> datetime:
    return datetime(2026, 7, 20, hour, 0, tzinfo=UTC)


def test_shift_valid():
    s = Shift("u1", _dt(8), _dt(16))
    assert s.scheduling_group_id is None


def test_shift_rejects_naive_datetime():
    with pytest.raises(InvalidShift):
        Shift("u1", datetime(2026, 7, 20, 8, 0), _dt(16))


def test_shift_rejects_end_before_start():
    with pytest.raises(InvalidShift):
        Shift("u1", _dt(16), _dt(8))


def test_shift_rejects_zero_length():
    with pytest.raises(InvalidShift):
        Shift("u1", _dt(8), _dt(8))


def test_shift_rejects_longer_than_24h():
    start = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    end = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)  # 25h
    with pytest.raises(InvalidShift):
        Shift("u1", start, end)


def test_week_schedule_is_empty():
    assert WeekSchedule("u1", date(2026, 7, 20)).is_empty
    filled = WeekSchedule("u1", date(2026, 7, 20), (Shift("u1", _dt(8), _dt(16)),))
    assert not filled.is_empty


def test_week_schedule_rejects_non_monday():
    with pytest.raises(ValueError):
        WeekSchedule("u1", date(2026, 7, 21))  # wtorek


def test_member_requires_nonempty_fields():
    with pytest.raises(ValueError):
        Member("", "Ala")
    with pytest.raises(ValueError):
        Member("u1", "")
