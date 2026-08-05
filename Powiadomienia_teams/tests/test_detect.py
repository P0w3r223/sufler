from datetime import datetime, timezone

from powiadomienia_teams.domain.models import Member, Shift, TimeOff
from powiadomienia_teams.reminders.detect import (
    member_filled_week,
    members_without_shifts,
    off_weekdays_by_member,
)

UTC = timezone.utc
# target_monday: lokalna północ poniedziałku docelowego tygodnia. UTC (nie Europe/Warsaw) celowo —
# eliminuje przesunięcie DST z rachunku i utrzymuje granice dni zgodne z kalendarzowymi datami
# poniżej, bez wpływu na testowaną logikę (`off_weekdays_by_member` i tak ignoruje `tz`).
MONDAY = datetime(2026, 7, 20, tzinfo=UTC)


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


def test_member_with_full_week_time_off_is_not_missing():
    """Urlop na CAŁY tydzień roboczy (pon–pt) = grafik uzupełniony; prośba byłaby nagabywaniem."""
    members = [Member("u1", "A"), Member("u2", "B")]
    off_by_member = off_weekdays_by_member([_time_off("u2", day=20, days=5)], MONDAY, UTC)
    out = members_without_shifts(members, [], off_by_member)
    assert [m.user_id for m in out] == ["u1"]


def test_partial_time_off_still_needs_a_nudge():
    """Urlop CZĘŚCIOWY (np. tylko piątek) NIE wycisza już prośby o pozostałe dni."""
    members = [Member("u1", "A")]
    off_by_member = off_weekdays_by_member([_time_off("u1", day=24, days=1)], MONDAY, UTC)
    out = members_without_shifts(members, [], off_by_member)
    assert [m.user_id for m in out] == ["u1"]


def test_shifts_and_full_time_off_cover_jointly():
    members = [Member("u1", "A"), Member("u2", "B"), Member("u3", "C")]
    off_by_member = off_weekdays_by_member([_time_off("u2", day=20, days=5)], MONDAY, UTC)
    out = members_without_shifts(members, [_shift("u1")], off_by_member)
    assert [m.user_id for m in out] == ["u3"]


def test_no_off_by_member_defaults_to_empty_map():
    """`off_by_member=None` (domyślne) zachowuje się jak pusta mapa — nikt nie jest pokryty."""
    members = [Member("u1", "A")]
    assert members_without_shifts(members, []) == members


class TestOffWeekdaysByMember:
    def test_maps_single_day_off(self):
        off = _time_off("u1", day=24, days=1)  # piątek 2026-07-24
        result = off_weekdays_by_member([off], MONDAY, UTC)
        assert result == {"u1": frozenset({4})}  # piątek = weekday 4

    def test_maps_full_week_off(self):
        off = _time_off("u1", day=20, days=5)  # pon–pt
        result = off_weekdays_by_member([off], MONDAY, UTC)
        assert result["u1"] == frozenset({0, 1, 2, 3, 4})

    def test_no_time_off_gives_empty_map(self):
        assert off_weekdays_by_member([], MONDAY, UTC) == {}

    def test_multiple_members_kept_separate(self):
        offs = [_time_off("u1", day=20, days=1), _time_off("u2", day=21, days=1)]
        result = off_weekdays_by_member(offs, MONDAY, UTC)
        assert result == {"u1": frozenset({0}), "u2": frozenset({1})}

    def test_weekend_day_off_is_mapped_too(self):
        """Nie ograniczamy się do dni roboczych — mapa opisuje CAŁY tydzień (0–6)."""
        off = _time_off("u1", day=26, days=1)  # niedziela 2026-07-26
        result = off_weekdays_by_member([off], MONDAY, UTC)
        assert result == {"u1": frozenset({6})}


class TestMemberFilledWeek:
    def test_true_when_has_shift(self):
        assert member_filled_week("u1", [_shift("u1")], frozenset()) is True

    def test_true_when_full_week_off(self):
        assert member_filled_week("u1", [], frozenset({0, 1, 2, 3, 4})) is True

    def test_false_when_neither(self):
        assert member_filled_week("u1", [], frozenset()) is False

    def test_false_when_only_partial_off(self):
        assert member_filled_week("u1", [], frozenset({4})) is False

    def test_ignores_other_members_shifts(self):
        assert member_filled_week("u1", [_shift("ghost")], frozenset()) is False
