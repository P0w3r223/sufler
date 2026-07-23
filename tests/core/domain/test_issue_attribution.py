"""Przypisanie godzin do zgłoszeń: klucze per dzień + podział minut z koszykiem (ADR 0036)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from workmate.core.domain.issue_attribution import issue_keys_by_day, split_day_minutes
from workmate.core.domain.worklog import Commit

WARSAW = ZoneInfo("Europe/Warsaw")


def _commit(message: str, at: datetime) -> Commit:
    return Commit(sha="x", message=message, authored_at=at)


def _utc(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=timezone.utc)


# --- issue_keys_by_day ------------------------------------------------------------


def test_keys_bucketed_by_local_day_in_order() -> None:
    commits = [
        _commit("WT-1 fix", _utc(15, 8)),
        _commit("WT-2 feat", _utc(15, 10)),
        _commit("OPS-9 chore", _utc(16, 9)),
    ]
    by_day = issue_keys_by_day(commits, tz=WARSAW)
    assert by_day[date(2026, 7, 15)] == ["WT-1", "WT-2"]
    assert by_day[date(2026, 7, 16)] == ["OPS-9"]


def test_keyless_commits_skipped_and_keys_deduped() -> None:
    commits = [
        _commit("bez klucza", _utc(15, 8)),
        _commit("WT-1 oraz WT-1 znowu", _utc(15, 9)),
        _commit("WT-1 dalej", _utc(15, 10)),
    ]
    assert issue_keys_by_day(commits, tz=WARSAW) == {date(2026, 7, 15): ["WT-1"]}


def test_local_day_uses_timezone() -> None:
    # 22:30 UTC 15 → 00:30 CEST 16 → klucz trafia na 16-go, nie 15-go.
    by_day = issue_keys_by_day([_commit("WT-9", _utc(15, 22, 30))], tz=WARSAW)
    assert by_day == {date(2026, 7, 16): ["WT-9"]}


# --- split_day_minutes ------------------------------------------------------------


def test_no_keys_go_to_fallback() -> None:
    assert split_day_minutes(480, [], fallback_issue="BIAP-1") == [("BIAP-1", 480)]


def test_single_key_gets_all_minutes() -> None:
    assert split_day_minutes(480, ["WT-1"], fallback_issue="BIAP-1") == [("WT-1", 480)]


def test_multiple_keys_split_evenly_remainder_to_first() -> None:
    # 100 min / 3 klucze = 34, 33, 33 (reszta 1 do pierwszego); suma == 100.
    assert split_day_minutes(100, ["A", "B", "C"], fallback_issue="X") == [
        ("A", 34),
        ("B", 33),
        ("C", 33),
    ]


def test_duplicate_keys_treated_as_one() -> None:
    assert split_day_minutes(60, ["A", "A", "B"], fallback_issue="X") == [("A", 30), ("B", 30)]


def test_zero_shares_dropped_when_more_keys_than_minutes() -> None:
    # 2 min / 3 klucze → 1, 1, 0 → zerowy udział pomijamy (bez pustego wiersza).
    assert split_day_minutes(2, ["A", "B", "C"], fallback_issue="X") == [("A", 1), ("B", 1)]


def test_zero_minutes_yields_nothing() -> None:
    assert split_day_minutes(0, ["A"], fallback_issue="X") == []
