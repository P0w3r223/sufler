"""Testy parsera znaczników czasu na granicy Jiry/GitHuba (``core/domain/jira_time.py``).

Parser stoi na granicy z zewnętrznym API i jest współdzielony przez dwie ścieżki (echo zdarzeń
Jiry, mapowanie commitów GitHuba, ``worklog.map_github_commits``). Jego kontraktem jest kierunek
awarii: wartość niezrozumiała ma dać ``None``, a nie wyjątek — bo wołający mają zachowanie
awaryjne (pominięcie wpisu), a nieudane echo nie może wywrócić udanej operacji.

Drugą własnością kontraktu jest ŚWIADOMOŚĆ STREFY każdego zwróconego znacznika — wynik wpada
do porównań w ``worklog``, a mieszanie naiwnych ze świadomymi kończy się tam ``TypeError``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from workmate.core.domain.jira_time import parse_jira_timestamp

_CEST = timezone(timedelta(hours=2))


def test_jira_default_shape_with_milliseconds_and_offset_without_colon():
    """Domyślny kształt Jiry — ``fromisoformat`` na 3.10 go NIE łyka, stąd jawny format."""
    got = parse_jira_timestamp("2026-07-20T09:15:00.000+0200")

    assert got == datetime(2026, 7, 20, 9, 15, tzinfo=_CEST)


def test_jira_shape_without_milliseconds():
    assert parse_jira_timestamp("2026-07-20T09:15:00+0200") == datetime(
        2026, 7, 20, 9, 15, tzinfo=_CEST
    )


def test_iso_offset_with_colon_falls_through_to_fromisoformat():
    """Trzecia gałąź parsera: wariant z dwukropkiem w offsecie domyka ``fromisoformat``."""
    assert parse_jira_timestamp("2026-07-20T09:15:00+02:00") == datetime(
        2026, 7, 20, 9, 15, tzinfo=_CEST
    )


def test_github_utc_z_suffix_uses_the_same_parser():
    """``map_github_commits`` woła TEN SAM parser — ``Z`` musi dawać czas świadomy strefy."""
    got = parse_jira_timestamp("2026-07-20T09:15:00Z")

    assert got == datetime(2026, 7, 20, 9, 15, tzinfo=UTC)
    assert got is not None and got.tzinfo is not None


def test_surrounding_whitespace_is_trimmed_before_parsing():
    assert parse_jira_timestamp("  2026-07-20T09:15:00.000+0200  ") == datetime(
        2026, 7, 20, 9, 15, tzinfo=_CEST
    )


@pytest.mark.parametrize(
    "value",
    ["", None, 0, [], {}],
    ids=["pusty-napis", "none", "zero", "pusta-lista", "pusty-slownik"],
)
def test_falsy_values_are_none_not_an_exception(value):
    """Puste pole odpowiedzi API to normalka — wołający pomija wpis, nie wywraca rundy."""
    assert parse_jira_timestamp(value) is None


@pytest.mark.parametrize(
    "value",
    ["bzdura", "20/07/2026", "2026-13-45T99:99:99Z", "wczoraj"],
)
def test_unparseable_values_are_none_not_an_exception(value):
    """Kierunek awarii: nie znam znacznika → ``None``. Wyjątek zabiłby ścieżkę ODCZYTU."""
    assert parse_jira_timestamp(value) is None


def test_non_string_input_is_coerced_before_parsing():
    """Wołający podaje surowe pole JSON-a — parser bierze ``Any`` i sam je rzutuje."""

    class _Ma_str:
        def __str__(self) -> str:
            return "2026-07-20T09:15:00Z"

    assert parse_jira_timestamp(_Ma_str()) == datetime(2026, 7, 20, 9, 15, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    ["2026-07-20T09:15:00", "2026-07-20 09:15:00", "2026-07-20"],
    ids=["iso-bez-strefy", "spacja-zamiast-T", "sama-data"],
)
def test_offsetless_input_is_anchored_in_UTC_not_returned_NAIVE(value: str):
    """Kontrakt parsera to „aware ``datetime``" — także dla wejścia bez offsetu.

    Wejście bez strefy przechodzi gałęzią ``fromisoformat`` i wracało NAIWNE. To nie była
    hipoteza: mieszana partia commitów (jeden ze znacznikiem ``Z``, jeden bez strefy) wywracała
    ``worklog.group_sessions`` na ``TypeError: can't compare offset-naive and offset-aware
    datetimes`` — czyli na tym, czemu ``map_github_commits`` obiecuje zapobiegać („jeden dziwny
    commit nie może wywrócić raportu"). Kotwicą jest UTC, bo oba API deklarują czas uniwersalny;
    zgadywanie strefy lokalnej przesuwałoby wpisy o godzinę lub dwie.
    """
    got = parse_jira_timestamp(value)

    assert got is not None and got.tzinfo is not None
    assert got.utcoffset() == timedelta(0)


def test_a_mixed_batch_stays_COMPARABLE_which_is_the_point_of_anchoring():
    """Sam „tzinfo nie jest None" nie oddaje szkody — szkodą było porównanie rzucające wyjątek."""
    ze_strefa = parse_jira_timestamp("2026-07-20T09:15:00Z")
    bez_strefy = parse_jira_timestamp("2026-07-20T10:15:00")

    assert ze_strefa is not None and bez_strefy is not None
    assert ze_strefa < bez_strefy  # przed naprawą: TypeError
