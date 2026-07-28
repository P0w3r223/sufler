"""Serwis self-service: minuty Shifts × klucze/opisy z submisji, izolacja nadawcy, okno z JSON-a."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from workmate.core.application.selfservice_worklog import (
    REASON_NO_WORK,
    REASON_OK,
    REASON_REJECTED,
    SelfServiceWorklog,
    build_selfservice_timesheet,
    handle_submission,
)
from workmate.core.domain.shift_hours import ShiftBlock
from workmate.core.domain.submitted_summary import SubmittedSummary
from workmate.core.domain.timesheet import Person

WARSAW = ZoneInfo("Europe/Warsaw")


class FakeShiftSource:
    def __init__(self, blocks: list[ShiftBlock]) -> None:
        self._blocks = blocks

    def read_blocks(self) -> list[ShiftBlock]:
        return self._blocks


class RecordingSheetWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...], tuple[tuple[Any, ...], ...]]] = []

    def write(self, path: str, headers: tuple[str, ...], rows: tuple[tuple[Any, ...], ...]) -> None:
        self.calls.append((path, headers, rows))


def _block(user_id: str, start_hour: int, end_hour: int, *, day: int = 15) -> ShiftBlock:
    return ShiftBlock(
        user_id=user_id,
        start=datetime(2026, 7, day, start_hour, tzinfo=timezone.utc),
        end=datetime(2026, 7, day, end_hour, tzinfo=timezone.utc),
    )


def _person(aad: str = "aad-1", *, git_email: str = "me@x.pl") -> Person:
    return Person(source_id="EMP-1", aad_user_id=aad, jira_user="me@x.pl", git_email=git_email)


def _summary(
    *,
    keys: dict[date, tuple[str, ...]] | None = None,
    comments: dict[date, str] | None = None,
    since: date = date(2026, 7, 13),
    until: date = date(2026, 7, 19),
) -> SubmittedSummary:
    return SubmittedSummary(
        person_git_email="me@x.pl",
        since=since,
        until=until,
        issue_keys_by_day=keys or {},
        comments_by_day=comments or {},
    )


def _service(source: FakeShiftSource, writer: RecordingSheetWriter) -> SelfServiceWorklog:
    return SelfServiceWorklog(
        source,
        writer,
        output_dir="/out",
        tz=WARSAW,
        fallback_issue="BIAP-1",
    )


# --- składanie zestawienia (pure) -------------------------------------------------


def test_shift_minutes_split_across_submitted_keys_with_comment() -> None:
    summary = _summary(
        keys={date(2026, 7, 15): ("WT-1", "WT-2")},
        comments={date(2026, 7, 15): "Robił WT-1 i WT-2."},
    )
    timesheet = build_selfservice_timesheet(
        _person(),
        [_block("aad-1", 6, 14)],  # 8h = 480 min lokalnie 15 lipca
        summary,
        tz=WARSAW,
        fallback_issue="BIAP-1",
    )
    assert [(e.issue_key, e.minutes) for e in timesheet.entries] == [("WT-1", 240), ("WT-2", 240)]
    assert {e.comment for e in timesheet.entries} == {"Robił WT-1 i WT-2."}
    assert timesheet.total_minutes == 480


def test_day_without_keys_goes_to_fallback_issue() -> None:
    timesheet = build_selfservice_timesheet(
        _person(),
        [_block("aad-1", 6, 14)],
        _summary(),  # brak kluczy
        tz=WARSAW,
        fallback_issue="BIAP-1",
    )
    (entry,) = timesheet.entries
    assert (entry.issue_key, entry.minutes) == ("BIAP-1", 480)


def test_other_peoples_shifts_are_ignored() -> None:
    timesheet = build_selfservice_timesheet(
        _person("aad-1"),
        [_block("aad-1", 6, 10), _block("aad-obcy", 6, 14)],  # blok kolegi w tym samym zaciągu
        _summary(),
        tz=WARSAW,
        fallback_issue="BIAP-1",
    )
    assert {e.source_id for e in timesheet.entries} == {"EMP-1"}
    assert timesheet.total_minutes == 240  # tylko 4h nadawcy, nie 8h kolegi


def test_until_is_inclusive_last_day_kept() -> None:
    # until = niedziela 19; blok w niedzielę musi wejść (okno półotwarte do 20).
    timesheet = build_selfservice_timesheet(
        _person(),
        [_block("aad-1", 6, 10, day=19)],
        _summary(until=date(2026, 7, 19)),
        tz=WARSAW,
        fallback_issue="BIAP-1",
    )
    assert timesheet.total_minutes == 240
    assert timesheet.entries[0].day == date(2026, 7, 19)


def test_week_label_from_submission_window() -> None:
    timesheet = build_selfservice_timesheet(
        _person(),
        [_block("aad-1", 6, 14)],
        _summary(),
        tz=WARSAW,
        fallback_issue="BIAP-1",
    )
    assert timesheet.week_label == "2026-W29"


# --- serwis: zapis arkusza --------------------------------------------------------


def test_run_writes_sheet_and_returns_path() -> None:
    writer = RecordingSheetWriter()
    result = _service(FakeShiftSource([_block("aad-1", 6, 14)]), writer).run(_person(), _summary())
    assert result.file_path.startswith("/out/worklog_")
    assert result.file_path.endswith("_2026-w29.xlsx")
    assert len(writer.calls) == 1
    path, headers, rows = writer.calls[0]
    assert path == result.file_path
    assert headers[0] == "Issue Key/ID"
    assert len(rows) == 1  # jeden wiersz: 8h na koszyk


def test_run_without_hours_writes_nothing() -> None:
    writer = RecordingSheetWriter()
    # Bloki nadawcy poza oknem submisji → brak godzin.
    result = _service(FakeShiftSource([_block("aad-1", 6, 14, day=5)]), writer).run(
        _person(), _summary()
    )
    assert result.file_path == ""
    assert writer.calls == []
    assert not result.timesheet.worked()


# --- handler: parsuj → zweryfikuj → złóż → odpowiedź -------------------------------


def _raw_payload(
    *,
    person: str = "me@x.pl",
    since: str = "2026-07-13",
    until: str = "2026-07-19",
    days: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {"person": person, "since": since, "until": until, "days": days or []}


def test_handle_success_produces_sheet_and_reply() -> None:
    writer = RecordingSheetWriter()
    outcome = handle_submission(
        _service(FakeShiftSource([_block("aad-1", 6, 14)]), writer),
        _person(),
        _raw_payload(days=[{"date": "2026-07-15", "commits": [{"message": "WT-1 x"}]}]),
    )
    assert outcome.is_success
    assert outcome.reason == REASON_OK
    assert outcome.file_path.endswith(".xlsx")
    assert outcome.week_label == "2026-W29"
    assert outcome.total_minutes == 480
    assert "WT-1" in outcome.reply_html
    assert len(writer.calls) == 1


def test_handle_attachment_mode_omits_the_path_and_keeps_it_for_the_door() -> None:
    """A′4: z ``deliver_as_attachment`` treść mówi „w załączniku", ale ``file_path`` zostaje.

    Ścieżka jest potrzebna DRZWIOM (wczytują bajta do wgrania), tylko nie pokazujemy jej w treści.
    """
    writer = RecordingSheetWriter()
    outcome = handle_submission(
        _service(FakeShiftSource([_block("aad-1", 6, 14)]), writer),
        _person(),
        _raw_payload(days=[{"date": "2026-07-15", "commits": [{"message": "WT-1 x"}]}]),
        deliver_as_attachment=True,
    )
    assert outcome.is_success
    assert outcome.file_path.endswith(".xlsx")  # drzwi dostają ścieżkę do wczytania bajtów
    assert "załączniku" in outcome.reply_html
    assert "<code>" not in outcome.reply_html  # ścieżka NIE wyciekła do treści
    assert "/out/" not in outcome.reply_html


def test_handle_default_still_shows_the_path_fallback() -> None:
    """Domyślnie (bez flagi) zostaje fallback ścieżką — jak dotąd."""
    writer = RecordingSheetWriter()
    outcome = handle_submission(
        _service(FakeShiftSource([_block("aad-1", 6, 14)]), writer),
        _person(),
        _raw_payload(days=[{"date": "2026-07-15", "commits": [{"message": "WT-1 x"}]}]),
    )
    assert "<code>" in outcome.reply_html  # akapit ze ścieżką obecny


def test_handle_rejects_foreign_export_no_sheet() -> None:
    writer = RecordingSheetWriter()
    outcome = handle_submission(
        _service(FakeShiftSource([_block("aad-1", 6, 14)]), writer),
        _person(git_email="me@x.pl"),
        _raw_payload(person="kolega@x.pl"),  # cudzy eksport
    )
    assert not outcome.is_success
    assert outcome.reason == REASON_REJECTED
    assert "cudzego eksportu" in outcome.reply_html
    assert writer.calls == []  # nic nie zapisano


def test_handle_rejects_bad_shape() -> None:
    writer = RecordingSheetWriter()
    outcome = handle_submission(_service(FakeShiftSource([]), writer), _person(), ["nie", "obiekt"])
    assert outcome.reason == REASON_REJECTED
    assert writer.calls == []


def test_handle_no_hours_gives_shifts_hint_not_success() -> None:
    writer = RecordingSheetWriter()
    outcome = handle_submission(
        _service(FakeShiftSource([_block("aad-1", 6, 14, day=5)]), writer),  # poza oknem
        _person(),
        _raw_payload(),
    )
    assert not outcome.is_success
    assert outcome.reason == REASON_NO_WORK
    assert "Shifts" in outcome.reply_html
    assert writer.calls == []
