"""Testy matematyki tygodnia (ADR 0035) — termin piątkowy, okno raportowania, odporność na DST.

Zegar jest wstrzykiwany, więc wszystko jest deterministyczne. Przejścia czasu w Warszawie
sprawdzamy JAWNIE: to one decydują, do którego dnia trafi czyjaś godzina pracy w obcym systemie.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from workmate.core.domain.week import (
    FRIDAY,
    next_run,
    previous_run,
    reported_week,
    week_label,
    week_monday,
)

_TZ = ZoneInfo("Europe/Warsaw")


def _warsaw(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_TZ)


def _elapsed(start: datetime, end: datetime) -> timedelta:
    """Realnie upłynięty czas — przez UTC, bo odejmowanie w tej samej strefie pomija DST."""
    return end.astimezone(timezone.utc) - start.astimezone(timezone.utc)


# --- poniedziałek tygodnia -------------------------------------------------------


def test_week_monday_on_midweek_day() -> None:
    # Środa 15.07.2026 → poniedziałek 13.07 o północy lokalnej.
    assert week_monday(_warsaw(2026, 7, 15), _TZ) == _warsaw(2026, 7, 13, 0, 0)


def test_week_monday_on_monday_is_the_same_day() -> None:
    assert week_monday(_warsaw(2026, 7, 13, 23, 59), _TZ) == _warsaw(2026, 7, 13, 0, 0)


def test_week_monday_accepts_now_in_another_timezone() -> None:
    """23:30 UTC w niedzielę to już poniedziałek w Warszawie — liczy się strefa docelowa."""
    utc_sunday = datetime(2026, 7, 12, 23, 30, tzinfo=timezone.utc)
    assert week_monday(utc_sunday, _TZ) == _warsaw(2026, 7, 13, 0, 0)


# --- okno raportowania -----------------------------------------------------------


def test_reported_week_covers_the_week_that_is_already_closed() -> None:
    """Piątkowy przebieg raportuje tydzień POPRZEDNI — cały leży w przeszłości."""
    start, end = reported_week(_warsaw(2026, 7, 17, 16), _TZ)  # piątek W29
    assert start == _warsaw(2026, 7, 6, 0, 0)
    assert end == _warsaw(2026, 7, 13, 0, 0)


def test_weekend_hours_reach_a_report_instead_of_falling_through() -> None:
    """Regresja: przy oknie BIEŻĄCYM sobota i piątkowe popołudnie nie trafiały NIGDZIE.

    Przebieg w piątek 17.07 o 16:00 zamykał okno na 20.07, a kolejny (24.07) zaczynał je
    dopiero 20.07 — godziny z 18–19.07 (weekend) i z piątkowego wieczoru wypadały z OBU.
    Przy oknie zamkniętym każdy dzień należy dokładnie do jednego raportu.
    """
    _, end_now = reported_week(_warsaw(2026, 7, 17, 16), _TZ)
    start_next, _ = reported_week(_warsaw(2026, 7, 24, 16), _TZ)
    assert end_now == start_next  # zero luki między kolejnymi przebiegami
    saturday = _warsaw(2026, 7, 18)
    start_later, end_later = reported_week(_warsaw(2026, 7, 24, 16), _TZ)
    assert start_later <= saturday < end_later


def test_reported_week_is_half_open() -> None:
    """Koniec NIE wchodzi — inaczej sąsiednie tygodnie policzyłyby ten sam dzień dwa razy."""
    start, end = reported_week(_warsaw(2026, 7, 17), _TZ)
    next_start, _ = reported_week(_warsaw(2026, 7, 24), _TZ)
    assert end == next_start


def test_reported_week_spans_exactly_seven_days() -> None:
    start, end = reported_week(_warsaw(2026, 7, 17), _TZ)
    assert end - start == timedelta(days=7)


# --- najbliższy termin -----------------------------------------------------------


def test_next_run_finds_friday_later_this_week() -> None:
    assert next_run(_warsaw(2026, 7, 15), tz=_TZ) == _warsaw(2026, 7, 17, 16, 0)


def test_next_run_skips_to_following_week_after_the_deadline() -> None:
    assert next_run(_warsaw(2026, 7, 17, 16, 30), tz=_TZ) == _warsaw(2026, 7, 24, 16, 0)


def test_next_run_is_strict_at_the_exact_deadline() -> None:
    """Przebieg zakończony dokładnie o 16:00 nie może wyzwolić się od razu drugi raz."""
    assert next_run(_warsaw(2026, 7, 17, 16, 0), tz=_TZ) == _warsaw(2026, 7, 24, 16, 0)


def test_next_run_honours_custom_weekday_and_hour() -> None:
    assert next_run(_warsaw(2026, 7, 15), tz=_TZ, weekday=0, hour=9) == _warsaw(2026, 7, 20, 9, 0)


def test_friday_is_the_default_weekday() -> None:
    assert FRIDAY == 4
    assert next_run(_warsaw(2026, 7, 15), tz=_TZ).weekday() == FRIDAY


# --- poprzedni termin (nadrabianie) ----------------------------------------------


def test_previous_run_finds_the_deadline_that_just_passed() -> None:
    assert previous_run(_warsaw(2026, 7, 18, 10), tz=_TZ) == _warsaw(2026, 7, 17, 16, 0)


def test_previous_run_before_todays_deadline_goes_back_a_week() -> None:
    assert previous_run(_warsaw(2026, 7, 17, 9), tz=_TZ) == _warsaw(2026, 7, 10, 16, 0)


def test_previous_run_is_inclusive_at_the_exact_deadline() -> None:
    assert previous_run(_warsaw(2026, 7, 17, 16, 0), tz=_TZ) == _warsaw(2026, 7, 17, 16, 0)


def test_next_and_previous_run_bracket_now() -> None:
    now = _warsaw(2026, 7, 15, 13, 37)
    assert previous_run(now, tz=_TZ) <= now < next_run(now, tz=_TZ)


# --- zmiana czasu (DST) ----------------------------------------------------------


def test_deadline_stays_at_local_16_across_spring_forward() -> None:
    """Ostatni piątek marca 2026 jest przed zmianą, pierwszy kwietniowy po — obie 16:00 lokalnie."""
    before = next_run(_warsaw(2026, 3, 26), tz=_TZ)  # czwartek przed zmianą (29.03)
    after = next_run(_warsaw(2026, 4, 2), tz=_TZ)  # czwartek po zmianie
    assert before.hour == 16 and after.hour == 16
    assert before.utcoffset() != after.utcoffset()  # offset SIĘ zmienił, godzina lokalna nie


def test_deadline_stays_at_local_16_across_fall_back() -> None:
    before = next_run(_warsaw(2026, 10, 22), tz=_TZ)  # przed zmianą (25.10)
    after = next_run(_warsaw(2026, 10, 29), tz=_TZ)  # po zmianie
    assert before.hour == 16 and after.hour == 16
    assert before.utcoffset() != after.utcoffset()


def test_reported_week_boundaries_stay_at_local_midnight_across_fall_back() -> None:
    """Tydzień 19–25.10.2026 zawiera cofnięcie zegara — granice MUSZĄ zostać lokalną północą.

    To sedno wyboru ``ZoneInfo`` zamiast stałego offsetu: gdyby okno liczyć w bezwzględnych
    godzinach, koniec wypadłby o 23:00 i praca z niedzielnego wieczoru wypadłaby poza raport.
    """
    start, end = reported_week(_warsaw(2026, 10, 28), _TZ)
    assert (start.hour, end.hour) == (0, 0)
    assert end.date() == start.date() + timedelta(days=7)
    # Realnie tydzień trwa 169 h (doba cofnięcia ma 25 h) — granice i tak stoją o północy.
    # Mierzymy przez UTC: odejmowanie dat z TYM SAMYM ``tzinfo`` pomija strefę (dokumentacja
    # CPythona), więc dałoby gołe 7 dni i nie sprawdziłoby niczego.
    assert _elapsed(start, end) == timedelta(days=7, hours=1)


def test_reported_week_boundaries_stay_at_local_midnight_across_spring_forward() -> None:
    """Tydzień 23–29.03.2026 zawiera przesunięcie zegara w przód — realnie 167 h."""
    start, end = reported_week(_warsaw(2026, 4, 1), _TZ)
    assert (start.hour, end.hour) == (0, 0)
    assert _elapsed(start, end) == timedelta(days=7) - timedelta(hours=1)


# --- etykieta tygodnia -----------------------------------------------------------


def test_week_label_uses_iso_week_numbering() -> None:
    start, _ = reported_week(_warsaw(2026, 7, 24), _TZ)
    assert week_label(start) == "2026-W29"


def test_week_label_pads_single_digit_weeks() -> None:
    start, _ = reported_week(_warsaw(2026, 1, 15), _TZ)
    assert week_label(start) == "2026-W02"


@pytest.mark.parametrize("day", [20, 22, 24, 26])
def test_week_label_is_stable_across_the_whole_week(day: int) -> None:
    """Każdy dzień tygodnia daje TĘ SAMĄ etykietę — inaczej nazwa pliku zmieniałaby się w locie."""
    start, _ = reported_week(_warsaw(2026, 7, day), _TZ)
    assert week_label(start) == "2026-W29"
