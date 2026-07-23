"""Złożone źródło "shifts": mapowanie aad→osoba, koszyk, pusty komentarz, fail-closed (ADR 0036)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from workmate.core.application.shift_hours_source import ShiftsHoursSource
from workmate.core.domain.shift_hours import ShiftBlock
from workmate.core.domain.timesheet import Person
from workmate.core.domain.worklog import Commit

WARSAW = ZoneInfo("Europe/Warsaw")
SINCE = date(2026, 7, 13)
UNTIL = date(2026, 7, 20)


class FakeShiftSource:
    def __init__(self, blocks: list[ShiftBlock]) -> None:
        self._blocks = blocks

    def read_blocks(self) -> list[ShiftBlock]:
        return self._blocks


class FakeIdentities:
    def __init__(self, by_aad: dict[str, Person]) -> None:
        self._by_aad = by_aad

    def resolve_by_aad_user_id(self, aad_user_id: str) -> Person | None:
        return self._by_aad.get(aad_user_id)


class FakeCommitSource:
    def __init__(self, by_email: dict[str, list[Commit]]) -> None:
        self._by_email = by_email

    def commits_for(self, git_email: str, since: date, until: date) -> list[Commit]:
        return self._by_email.get(git_email, [])


def _block(user_id: str, start: datetime, end: datetime) -> ShiftBlock:
    return ShiftBlock(user_id=user_id, start=start, end=end)


def _utc(day: int, hour: int) -> datetime:
    return datetime(2026, 7, day, hour, tzinfo=timezone.utc)


def _person(source_id: str, aad: str, *, git_email: str = "") -> Person:
    return Person(source_id=source_id, aad_user_id=aad, jira_user="x@y.pl", git_email=git_email)


def _commit(message: str, at: datetime) -> Commit:
    return Commit(sha="x", message=message, authored_at=at)


def test_maps_aad_to_person_and_uses_fallback_issue() -> None:
    source = ShiftsHoursSource(
        FakeShiftSource([_block("aad-1", _utc(15, 6), _utc(15, 14))]),  # 8h
        FakeIdentities({"aad-1": _person("EMP-1", "aad-1")}),
        fallback_issue="BIAP-1",
        tz=WARSAW,
    )
    entries = source.read(SINCE, UNTIL)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.source_id == "EMP-1"
    assert entry.issue_key == "BIAP-1"  # cały czas na koszyk (klucze z commitów dopiero w S3)
    assert entry.minutes == 480
    assert entry.comment == ""  # opis z claude_summary dopiero w S4
    assert entry.day == date(2026, 7, 15)


def test_unknown_account_is_skipped_fail_closed() -> None:
    source = ShiftsHoursSource(
        FakeShiftSource([_block("aad-obcy", _utc(15, 6), _utc(15, 14))]),
        FakeIdentities({}),  # nikogo nie zna → fail-closed
        fallback_issue="BIAP-1",
        tz=WARSAW,
    )
    assert source.read(SINCE, UNTIL) == []


def test_out_of_window_shift_produces_no_entry() -> None:
    source = ShiftsHoursSource(
        FakeShiftSource([_block("aad-1", _utc(5, 6), _utc(5, 14))]),  # 5 lipca — poza oknem
        FakeIdentities({"aad-1": _person("EMP-1", "aad-1")}),
        fallback_issue="BIAP-1",
        tz=WARSAW,
    )
    assert source.read(SINCE, UNTIL) == []


# --- przypisanie issue z commitów (S3, ADR 0036) ----------------------------------


def test_day_minutes_split_across_commit_issue_keys() -> None:
    source = ShiftsHoursSource(
        FakeShiftSource([_block("aad-1", _utc(15, 6), _utc(15, 14))]),  # 8h = 480 min
        FakeIdentities({"aad-1": _person("EMP-1", "aad-1", git_email="me@x.pl")}),
        fallback_issue="BIAP-1",
        tz=WARSAW,
        commits=FakeCommitSource(
            {"me@x.pl": [_commit("WT-1 rano", _utc(15, 7)), _commit("WT-2 po", _utc(15, 12))]}
        ),
    )
    entries = source.read(SINCE, UNTIL)
    assert [(e.issue_key, e.minutes) for e in entries] == [("WT-1", 240), ("WT-2", 240)]
    assert {e.source_id for e in entries} == {"EMP-1"}


def test_keyless_commits_fall_back_to_bucket_issue() -> None:
    source = ShiftsHoursSource(
        FakeShiftSource([_block("aad-1", _utc(15, 6), _utc(15, 14))]),
        FakeIdentities({"aad-1": _person("EMP-1", "aad-1", git_email="me@x.pl")}),
        fallback_issue="BIAP-1",
        tz=WARSAW,
        commits=FakeCommitSource({"me@x.pl": [_commit("bez klucza jira", _utc(15, 9))]}),
    )
    (entry,) = source.read(SINCE, UNTIL)
    assert (entry.issue_key, entry.minutes) == ("BIAP-1", 480)


def test_person_without_git_email_gets_fallback() -> None:
    source = ShiftsHoursSource(
        FakeShiftSource([_block("aad-1", _utc(15, 6), _utc(15, 14))]),
        FakeIdentities({"aad-1": _person("EMP-1", "aad-1")}),  # brak git_email
        fallback_issue="BIAP-1",
        tz=WARSAW,
        commits=FakeCommitSource({"me@x.pl": [_commit("WT-1", _utc(15, 9))]}),
    )
    (entry,) = source.read(SINCE, UNTIL)
    assert entry.issue_key == "BIAP-1"  # bez e-maila nie sięgamy po commity


def test_no_commit_source_all_on_fallback() -> None:
    source = ShiftsHoursSource(
        FakeShiftSource([_block("aad-1", _utc(15, 6), _utc(15, 14))]),
        FakeIdentities({"aad-1": _person("EMP-1", "aad-1", git_email="me@x.pl")}),
        fallback_issue="BIAP-1",
        tz=WARSAW,
        commits=None,  # GitHub nieskonfigurowany
    )
    (entry,) = source.read(SINCE, UNTIL)
    assert entry.issue_key == "BIAP-1"


def test_commits_fetched_once_per_person_and_cached() -> None:
    calls: list[str] = []

    class CountingCommits:
        def commits_for(self, git_email: str, since: date, until: date) -> list[Commit]:
            calls.append(git_email)
            return [_commit("WT-1", _utc(15, 9))]

    source = ShiftsHoursSource(
        FakeShiftSource(
            [
                _block("aad-1", _utc(15, 6), _utc(15, 10)),  # dwa dni tej samej osoby
                _block("aad-1", _utc(16, 6), _utc(16, 10)),
            ]
        ),
        FakeIdentities({"aad-1": _person("EMP-1", "aad-1", git_email="me@x.pl")}),
        fallback_issue="BIAP-1",
        tz=WARSAW,
        commits=CountingCommits(),
    )
    source.read(SINCE, UNTIL)
    assert calls == ["me@x.pl"]  # jeden zaciąg mimo dwóch dni


# --- opis dnia z claude_summary (S4, ADR 0036) ------------------------------------


class FakeSummaries:
    def __init__(self, by_email: dict[str, dict[date, str]]) -> None:
        self._by_email = by_email

    def comments_by_day(self, git_email: str, since: date, until: date) -> dict[date, str]:
        return self._by_email.get(git_email, {})


def test_day_comment_attached_to_every_issue_row() -> None:
    source = ShiftsHoursSource(
        FakeShiftSource([_block("aad-1", _utc(15, 6), _utc(15, 14))]),  # 8h → dwa klucze
        FakeIdentities({"aad-1": _person("EMP-1", "aad-1", git_email="me@x.pl")}),
        fallback_issue="BIAP-1",
        tz=WARSAW,
        commits=FakeCommitSource(
            {"me@x.pl": [_commit("WT-1", _utc(15, 7)), _commit("WT-2", _utc(15, 12))]}
        ),
        summaries=FakeSummaries({"me@x.pl": {date(2026, 7, 15): "Robił WT-1 i WT-2."}}),
    )
    entries = source.read(SINCE, UNTIL)
    assert [e.issue_key for e in entries] == ["WT-1", "WT-2"]
    assert {e.comment for e in entries} == {"Robił WT-1 i WT-2."}  # ten sam opis na obu wierszach


def test_no_summary_for_the_day_leaves_empty_comment() -> None:
    source = ShiftsHoursSource(
        FakeShiftSource([_block("aad-1", _utc(15, 6), _utc(15, 14))]),
        FakeIdentities({"aad-1": _person("EMP-1", "aad-1", git_email="me@x.pl")}),
        fallback_issue="BIAP-1",
        tz=WARSAW,
        summaries=FakeSummaries({"me@x.pl": {date(2026, 7, 16): "inny dzień"}}),
    )
    (entry,) = source.read(SINCE, UNTIL)
    assert entry.comment == ""


def test_no_summary_source_empty_comment() -> None:
    source = ShiftsHoursSource(
        FakeShiftSource([_block("aad-1", _utc(15, 6), _utc(15, 14))]),
        FakeIdentities({"aad-1": _person("EMP-1", "aad-1", git_email="me@x.pl")}),
        fallback_issue="BIAP-1",
        tz=WARSAW,
        summaries=None,
    )
    (entry,) = source.read(SINCE, UNTIL)
    assert entry.comment == ""


def test_person_without_git_email_has_empty_comment() -> None:
    source = ShiftsHoursSource(
        FakeShiftSource([_block("aad-1", _utc(15, 6), _utc(15, 14))]),
        FakeIdentities({"aad-1": _person("EMP-1", "aad-1")}),  # brak git_email
        fallback_issue="BIAP-1",
        tz=WARSAW,
        summaries=FakeSummaries({"me@x.pl": {date(2026, 7, 15): "nie sięgamy"}}),
    )
    (entry,) = source.read(SINCE, UNTIL)
    assert entry.comment == ""


def test_summaries_fetched_once_per_person_and_cached() -> None:
    calls: list[str] = []

    class CountingSummaries:
        def comments_by_day(self, git_email: str, since: date, until: date) -> dict[date, str]:
            calls.append(git_email)
            return {date(2026, 7, 15): "dzień 15", date(2026, 7, 16): "dzień 16"}

    source = ShiftsHoursSource(
        FakeShiftSource(
            [
                _block("aad-1", _utc(15, 6), _utc(15, 10)),  # dwa dni tej samej osoby
                _block("aad-1", _utc(16, 6), _utc(16, 10)),
            ]
        ),
        FakeIdentities({"aad-1": _person("EMP-1", "aad-1", git_email="me@x.pl")}),
        fallback_issue="BIAP-1",
        tz=WARSAW,
        summaries=CountingSummaries(),
    )
    entries = source.read(SINCE, UNTIL)
    assert calls == ["me@x.pl"]  # jeden zaciąg opisów mimo dwóch dni
    assert {e.day: e.comment for e in entries} == {
        date(2026, 7, 15): "dzień 15",
        date(2026, 7, 16): "dzień 16",
    }
