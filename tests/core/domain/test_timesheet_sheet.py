"""Testy projekcji arkusza WorklogPRO (ADR 0035) — format, strażniki, determinizm.

Ten moduł produkuje plik, który człowiek zaimportuje do Jiry. Po imporcie wpisy są
create-only i nieusuwalne narzędziem (ADR 0034), więc błąd tutaj jest praktycznie nieodwracalny —
stąd nacisk na strażniki tożsamości i jednoznaczność formatów.
"""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import pytest

from workmate.core.domain.guards import CrossPersonLeak
from workmate.core.domain.timesheet import Person, WorkEntry, build_timesheet
from workmate.core.domain.timesheet_sheet import (
    WORKLOGPRO_HEADERS,
    format_started,
    format_time_spent,
    project_sheet,
    sheet_filename,
)

_TZ = ZoneInfo("Europe/Warsaw")
_PERSON = Person(
    source_id="EMP-042",
    aad_user_id="8a1f",
    jira_user="mikolaj@example.org",
    display_name="Mikołaj Anonimowicz",
)


def _entry(day: int, issue: str, minutes: int, comment: str = "") -> WorkEntry:
    return WorkEntry(
        source_id="EMP-042",
        day=date(2026, 7, day),
        issue_key=issue,
        minutes=minutes,
        comment=comment,
    )


def _sheet(entries: list[WorkEntry], person: Person = _PERSON, **kw):
    timesheet = build_timesheet(
        person,
        entries,
        week_start=date(2026, 7, 13),
        week_end=date(2026, 7, 20),
        week_label="2026-W29",
    )
    return project_sheet(timesheet, start_hour=8, tz=_TZ, **kw)


# --- nagłówki (HIPOTEZA do potwierdzenia w Etapie 0) ------------------------------


def test_headers_match_the_confirmed_template() -> None:
    """STRAŻNIK schematu. Po pobraniu szablonu z WorklogPRO zaktualizuj TU i w module.

    Dopasowanie kolumn idzie PO NAZWIE, więc różnica w wielkości liter albo spacji unieważnia
    cały plik. Ten test jest jedynym miejscem, w którym hipoteza jest zapisana wprost.
    """
    assert WORKLOGPRO_HEADERS == (
        "Issue Key/ID",
        "User",
        "Time Spent",
        "Start Date & Time",
        "Comment",
    )


def test_sheet_carries_the_module_headers() -> None:
    assert _sheet([_entry(15, "WT-12", 180)]).headers == WORKLOGPRO_HEADERS


# --- format czasu ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(45, "45m"), (60, "1h"), (90, "1h 30m"), (180, "3h"), (455, "7h 35m")],
)
def test_time_spent_uses_jira_duration_notation(minutes: int, expected: str) -> None:
    assert format_time_spent(minutes) == expected


def test_time_spent_never_uses_day_units() -> None:
    """``1d`` znaczy co innego na każdej instancji (konfigurowalna długość dnia roboczego)."""
    assert "d" not in format_time_spent(24 * 60)


def test_time_spent_rejects_non_positive() -> None:
    with pytest.raises(ValueError, match="dodatni"):
        format_time_spent(0)


# --- znacznik startu -------------------------------------------------------------


def test_started_is_full_iso_with_explicit_offset() -> None:
    """Bez offsetu WorklogPRO użyłby strefy PRZEGLĄDARKI — plik byłby niedeterministyczny."""
    assert format_started(date(2026, 7, 15), start_hour=8, tz=_TZ) == "2026-07-15T08:00:00.000+0200"


def test_started_offset_follows_dst() -> None:
    zima = format_started(date(2026, 1, 15), start_hour=8, tz=_TZ)
    lato = format_started(date(2026, 7, 15), start_hour=8, tz=_TZ)
    assert zima.endswith("+0100") and lato.endswith("+0200")


def test_started_honours_configured_hour() -> None:
    assert format_started(date(2026, 7, 15), start_hour=9, tz=_TZ).startswith("2026-07-15T09:00")


# --- wiersze ---------------------------------------------------------------------


def test_one_row_per_entry() -> None:
    entries = [_entry(15, "WT-12", 180), _entry(15, "WT-14", 90), _entry(16, "WT-12", 60)]
    assert len(_sheet(entries).rows) == 3


def test_row_columns_line_up_with_headers() -> None:
    (row,) = _sheet([_entry(15, "WT-12", 90, "przegląd kodu")]).rows
    assert row == (
        "WT-12",
        "mikolaj@example.org",
        "1h 30m",
        "2026-07-15T08:00:00.000+0200",
        "przegląd kodu",
    )


def test_zero_minute_entries_are_skipped() -> None:
    """Wpis urlopowy nie jest pracą — WorklogPRO i tak by go odrzucił."""
    assert _sheet([_entry(15, "WT-12", 0), _entry(16, "WT-14", 60)]).rows == (
        ("WT-14", "mikolaj@example.org", "1h", "2026-07-16T08:00:00.000+0200", ""),
    )


def test_comment_prefix_is_prepended() -> None:
    (row,) = _sheet([_entry(15, "WT-12", 60, "praca")], comment_prefix="[auto] ").rows
    assert row[4] == "[auto] praca"


def test_long_comment_is_truncated() -> None:
    (row,) = _sheet([_entry(15, "WT-12", 60, "x" * 900)]).rows
    assert len(row[4]) == 500


def test_rows_are_deterministic_for_the_same_week() -> None:
    entries = [_entry(16, "WT-14", 60), _entry(15, "WT-99", 60)]
    assert _sheet(entries).rows == _sheet(list(reversed(entries))).rows


# --- strażniki tożsamości --------------------------------------------------------


def test_every_user_cell_carries_the_same_identity() -> None:
    rows = _sheet([_entry(15, "WT-12", 60), _entry(16, "WT-14", 60)]).rows
    assert {row[1] for row in rows} == {"mikolaj@example.org"}


def test_projection_rejects_a_timesheet_holding_foreign_entries() -> None:
    """Obejście ``build_timesheet`` nie może przejść — to ostatni strażnik przed plikiem."""
    timesheet = build_timesheet(
        _PERSON,
        [_entry(15, "WT-12", 60)],
        week_start=date(2026, 7, 13),
        week_end=date(2026, 7, 20),
    )
    obcy = WorkEntry(source_id="EMP-017", day=date(2026, 7, 15), issue_key="WT-99", minutes=60)
    podmieniony = timesheet.model_copy(update={"entries": (*timesheet.entries, obcy)})
    with pytest.raises(CrossPersonLeak, match="EMP-017"):
        project_sheet(podmieniony, start_hour=8, tz=_TZ)


# --- nazwa pliku -----------------------------------------------------------------


def test_filename_is_ascii_slug_with_week_label() -> None:
    timesheet = build_timesheet(
        _PERSON,
        [_entry(15, "WT-12", 60)],
        week_start=date(2026, 7, 13),
        week_end=date(2026, 7, 20),
        week_label="2026-W29",
    )
    assert sheet_filename(timesheet) == "worklog_mikolaj-anonimowicz_2026-w29.xlsx"


def test_filename_strips_polish_diacritics() -> None:
    person = _PERSON.model_copy(update={"display_name": "Łukasz Żółć"})
    timesheet = build_timesheet(
        person, [], week_start=date(2026, 7, 13), week_end=date(2026, 7, 20), week_label="2026-W29"
    )
    assert sheet_filename(timesheet) == "worklog_lukasz-zolc_2026-w29.xlsx"


def test_filename_falls_back_to_source_id() -> None:
    person = _PERSON.model_copy(update={"display_name": ""})
    timesheet = build_timesheet(
        person, [], week_start=date(2026, 7, 13), week_end=date(2026, 7, 20), week_label="2026-W29"
    )
    assert sheet_filename(timesheet).startswith("worklog_emp-042_")
