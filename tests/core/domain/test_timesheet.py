"""Testy domeny karty czasu (ADR 0035) — agregacja, predykat ``worked`` i izolacja osób.

Wszystko w pamięci, bez I/O. Najważniejsze asercje modułu dotyczą nie arytmetyki, lecz tego,
żeby dane jednej osoby NIGDY nie trafiły do zestawienia innej.
"""

from __future__ import annotations

from datetime import date

import pytest

from workmate.core.domain.guards import CrossPersonLeak, assert_single_person
from workmate.core.domain.timesheet import (
    Person,
    TimesheetError,
    WorkEntry,
    build_timesheet,
    group_by_source_id,
    hours_of,
)

_WEEK_START, _WEEK_END = date(2026, 7, 13), date(2026, 7, 20)

_MIKOLAJ = Person(
    source_id="EMP-042",
    aad_user_id="8a1f-mikolaj",
    jira_user="mikolaj@example.org",
    display_name="Mikołaj Anonimowicz",
)
_PIOTR = Person(
    source_id="EMP-017",
    aad_user_id="4788-piotr",
    jira_user="piotr.alt@example.org",
    display_name="Piotr Cząstkiewicz",
)


def _entry(source_id: str, day: int, issue: str, minutes: int) -> WorkEntry:
    return WorkEntry(source_id=source_id, day=date(2026, 7, day), issue_key=issue, minutes=minutes)


def _sheet(entries: list[WorkEntry], person: Person = _MIKOLAJ, **kw):
    return build_timesheet(
        person, entries, week_start=_WEEK_START, week_end=_WEEK_END, week_label="2026-W29", **kw
    )


# --- predykat „pracował" ---------------------------------------------------------


def test_person_without_entries_did_not_work() -> None:
    """Bramka wysyłki: brak wpisów = brak wiadomości w piątek."""
    assert _sheet([]).worked() is False


def test_person_with_only_zero_minute_entries_did_not_work() -> None:
    """Źródło potrafi zwrócić zerowe wpisy dla urlopu — to nie jest praca."""
    assert _sheet([_entry("EMP-042", 15, "WT-12", 0)]).worked() is False


def test_person_with_real_entries_worked() -> None:
    assert _sheet([_entry("EMP-042", 15, "WT-12", 180)]).worked() is True


# --- izolacja osób ---------------------------------------------------------------


def test_build_timesheet_keeps_only_the_persons_own_entries() -> None:
    entries = [_entry("EMP-042", 15, "WT-12", 180), _entry("EMP-017", 15, "WT-99", 240)]
    sheet = _sheet(entries)
    assert [e.source_id for e in sheet.entries] == ["EMP-042"]
    assert sheet.total_minutes == 180


def test_guard_rejects_foreign_entries() -> None:
    with pytest.raises(CrossPersonLeak, match="EMP-017"):
        assert_single_person("EMP-042", ["EMP-042", "EMP-017"])


def test_guard_passes_for_clean_set() -> None:
    assert_single_person("EMP-042", ["EMP-042", "EMP-042"])


def test_guard_passes_for_empty_set() -> None:
    assert_single_person("EMP-042", [])


def test_guard_names_every_foreign_owner_once() -> None:
    with pytest.raises(CrossPersonLeak) as exc:
        assert_single_person("EMP-042", ["EMP-001", "EMP-002", "EMP-001"])
    assert exc.value.args[0].count("EMP-001") == 1


# --- okno tygodnia ---------------------------------------------------------------


def test_entries_outside_the_window_are_dropped() -> None:
    entries = [
        _entry("EMP-042", 12, "WT-12", 60),  # niedziela poprzedniego tygodnia
        _entry("EMP-042", 15, "WT-12", 60),
        _entry("EMP-042", 20, "WT-12", 60),  # poniedziałek następnego
    ]
    assert _sheet(entries).total_minutes == 60


def test_window_is_half_open_at_the_end() -> None:
    """``week_end`` NIE wchodzi — inaczej dzień styku wpadłby do dwóch tygodni."""
    assert _sheet([_entry("EMP-042", 19, "WT-12", 60)]).total_minutes == 60
    assert _sheet([_entry("EMP-042", 20, "WT-12", 60)]).total_minutes == 0


# --- sumy ------------------------------------------------------------------------


def test_daily_totals_sum_to_the_grand_total() -> None:
    entries = [
        _entry("EMP-042", 15, "WT-12", 180),
        _entry("EMP-042", 15, "WT-14", 90),
        _entry("EMP-042", 16, "WT-12", 150),
    ]
    sheet = _sheet(entries)
    assert sum(d.minutes for d in sheet.by_day) == sheet.total_minutes == 420


def test_issue_totals_sum_to_the_grand_total() -> None:
    entries = [
        _entry("EMP-042", 15, "WT-12", 180),
        _entry("EMP-042", 16, "WT-12", 150),
        _entry("EMP-042", 16, "WT-14", 90),
    ]
    sheet = _sheet(entries)
    assert sum(i.minutes for i in sheet.by_issue) == sheet.total_minutes


def test_issue_totals_are_ordered_by_time_descending() -> None:
    entries = [_entry("EMP-042", 15, "WT-14", 60), _entry("EMP-042", 16, "WT-12", 300)]
    assert _sheet(entries).by_issue[0].issue_key == "WT-12"


def test_days_are_ordered_ascending() -> None:
    entries = [_entry("EMP-042", 17, "WT-12", 60), _entry("EMP-042", 14, "WT-12", 60)]
    assert [d.day.day for d in _sheet(entries).by_day] == [14, 17]


def test_day_lists_its_distinct_issue_keys() -> None:
    entries = [
        _entry("EMP-042", 15, "WT-12", 60),
        _entry("EMP-042", 15, "WT-14", 60),
        _entry("EMP-042", 15, "WT-12", 30),
    ]
    assert _sheet(entries).by_day[0].issue_keys == ("WT-12", "WT-14")


def test_entries_are_sorted_for_a_stable_sheet() -> None:
    """Kolejność wierszy musi być deterministyczna — inaczej ten sam tydzień da inny plik."""
    entries = [_entry("EMP-042", 16, "WT-14", 60), _entry("EMP-042", 15, "WT-99", 60)]
    order = [(e.day.day, e.issue_key) for e in _sheet(entries).entries]
    assert order == [(15, "WT-99"), (16, "WT-14")]


def test_hours_are_derived_from_minutes() -> None:
    assert hours_of(90) == 1.5
    assert _sheet([_entry("EMP-042", 15, "WT-12", 150)]).total_hours == 2.5


# --- kontrola jakości danych -----------------------------------------------------


def test_negative_minutes_are_rejected() -> None:
    with pytest.raises(TimesheetError, match="ujemny czas"):
        _sheet([_entry("EMP-042", 15, "WT-12", -60)])


def test_impossible_day_is_rejected_when_a_cap_is_configured() -> None:
    """Zacięte źródło nie może rozesłać ludziom nieprawdopodobnych liczb."""
    with pytest.raises(TimesheetError, match="powyżej limitu"):
        _sheet([_entry("EMP-042", 15, "WT-12", 20 * 60)], max_minutes_per_day=16 * 60)


def test_cap_counts_the_whole_day_not_single_entries() -> None:
    entries = [_entry("EMP-042", 15, "WT-12", 10 * 60), _entry("EMP-042", 15, "WT-14", 8 * 60)]
    with pytest.raises(TimesheetError, match="powyżej limitu"):
        _sheet(entries, max_minutes_per_day=16 * 60)


def test_zero_cap_disables_the_check() -> None:
    assert _sheet([_entry("EMP-042", 15, "WT-12", 30 * 60)], max_minutes_per_day=0).worked()


# --- grupowanie ------------------------------------------------------------------


def test_group_by_source_id_splits_the_batch() -> None:
    entries = [
        _entry("EMP-042", 15, "WT-12", 60),
        _entry("EMP-017", 15, "WT-99", 60),
        _entry("EMP-042", 16, "WT-12", 60),
    ]
    grouped = group_by_source_id(entries)
    assert set(grouped) == {"EMP-042", "EMP-017"}
    assert len(grouped["EMP-042"]) == 2


def test_timesheet_carries_person_and_week_identity() -> None:
    sheet = _sheet([_entry("EMP-017", 15, "WT-12", 60)], person=_PIOTR)
    assert sheet.person.jira_user == "piotr.alt@example.org"
    assert sheet.week_label == "2026-W29"
