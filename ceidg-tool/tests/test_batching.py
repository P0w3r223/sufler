"""Planer podziału na partie (ADR-0008, decyzja 3): rozłączność, eskalacja, powtarzalność.

Moduł jest czysty, więc wszystko sprawdzamy bez bazy i bez sieci. Najważniejsze są dwie
własności, na których opiera się wznawianie: partie **pokrywają dokładnie** zadany zakres
(żaden wpis nie wypada po cichu) i plan jest **deterministyczny** — te same kryteria po
przerwaniu dają te same odciski, więc pobrane partie da się rozpoznać i pominąć.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ceidg_tool.batching import (
    DATE_FLOOR,
    GRANULARITIES,
    Batch,
    BatchPlan,
    Granularity,
    plan_batches,
    refine,
)
from tests.support import criteria

TODAY = date(2026, 9, 5)
THRESHOLD = 50_000
DAY = timedelta(days=1)

# Jedna pełna dekada: 1 partia dekadą, 10 latami, 40 kwartałami, 120 miesiącami.
DECADE_OD = date(2020, 1, 1)
DECADE_DO = date(2029, 12, 31)


def plan_for(count: int, *, od: date = DECADE_OD, do: date = DECADE_DO) -> BatchPlan:
    return plan_batches(
        criteria(wojewodztwo="mazowieckie", data_od=od, data_do=do),
        count,
        today=TODAY,
        threshold=THRESHOLD,
    )


def test_batches_tile_the_requested_range_without_gap_or_overlap() -> None:
    """Rozłączne i wyczerpujące: koniec partii + 1 dzień = początek następnej."""
    plan = plan_for(500_000)

    assert plan.batches[0].od == DECADE_OD
    assert plan.batches[-1].do == DECADE_DO
    for earlier, later in zip(plan.batches, plan.batches[1:], strict=False):
        assert earlier.do + DAY == later.od, f"dziura albo zakładka przy {earlier.label}"
        assert earlier.od <= earlier.do


def test_batch_criteria_narrow_only_the_dates() -> None:
    """Partia to zwykłe `Criteria` — pozostałe filtry muszą przejść bez zmian."""
    source = criteria(
        wojewodztwo="mazowieckie", pkd="62.01.Z", data_od=DECADE_OD, data_do=DECADE_DO
    )
    plan = plan_batches(source, 500_000, today=TODAY, threshold=THRESHOLD)

    for batch in plan.batches:
        assert batch.criteria.wojewodztwo == source.wojewodztwo
        assert batch.criteria.pkd == source.pkd
        assert batch.criteria.data_od == batch.od
        assert batch.criteria.data_do == batch.do


@pytest.mark.parametrize(
    ("count", "expected_granularity", "expected_batches"),
    [
        (50_000, "dekada", 1),
        (500_000, "rok", 10),
        (2_000_000, "kwartal", 40),
        (6_000_000, "miesiac", 120),
    ],
    ids=["dekada", "rok", "kwartal", "miesiac"],
)
def test_granularity_escalates_only_as_far_as_the_threshold_forces(
    count: int, expected_granularity: str, expected_batches: int
) -> None:
    """Najgrubszy podział, przy którym średnia partia mieści się w progu — nie drobniejszy."""
    plan = plan_for(count)

    assert plan.granularity == expected_granularity
    assert len(plan.batches) == expected_batches
    assert plan.share <= THRESHOLD
    assert not plan.exhausted


def test_a_count_too_large_even_for_months_is_reported_as_exhausted() -> None:
    """Poniżej miesiąca nie schodzimy — plan mówi wprost, że sam podział nie wystarczy."""
    plan = plan_for(6_000_001)

    assert plan.granularity == GRANULARITIES[-1] == "miesiac"
    assert plan.exhausted is True
    assert plan.share > THRESHOLD


def test_missing_date_bounds_are_filled_from_the_floor_and_today() -> None:
    """Bez dat plan musi być powtarzalny między sesjami, więc granice są stałymi, nie pytaniem."""
    plan = plan_batches(
        criteria(wojewodztwo="mazowieckie"), 400_000, today=TODAY, threshold=THRESHOLD
    )

    assert plan.covers == (DATE_FLOOR, TODAY)
    assert plan.batches[0].od == DATE_FLOOR
    assert plan.batches[-1].do == TODAY


def test_replanning_the_same_input_yields_identical_fingerprints() -> None:
    """Na tym stoi wznawianie: po przerwaniu ten sam plan rozpoznaje pobrane partie."""
    first = plan_for(400_000)
    second = plan_for(400_000)

    assert [b.fingerprint() for b in first.batches] == [b.fingerprint() for b in second.batches]
    assert [b.label for b in first.batches] == [b.label for b in second.batches]


def test_fingerprints_are_unique_per_batch() -> None:
    """Gdyby dwie partie miały ten sam odcisk, jedna zostałaby uznana za już pobraną."""
    plan = plan_for(500_000)

    fingerprints = [b.fingerprint() for b in plan.batches]
    assert len(set(fingerprints)) == len(fingerprints)


@pytest.mark.parametrize(
    ("od", "do", "expected_labels"),
    [
        (date(2020, 1, 1), date(2029, 12, 31), ["2020-2029"]),
        (date(2021, 1, 1), date(2023, 12, 31), ["2021", "2022", "2023"]),
    ],
    ids=["dekada", "lata"],
)
def test_labels_are_stable_and_human_readable(
    od: date, do: date, expected_labels: list[str]
) -> None:
    """Etykiety trafiają do tabeli i do logu — ich brzmienie jest częścią kontraktu."""
    count = 10 if len(expected_labels) == 1 else 120_000
    plan = plan_for(count, od=od, do=do)

    assert [b.label for b in plan.batches] == expected_labels


def test_a_partial_period_is_clipped_to_the_requested_range() -> None:
    """Zakres kończący się w połowie roku nie może rozciągnąć partii poza to, o co proszono."""
    plan = plan_for(120_000, od=date(2021, 6, 15), do=date(2023, 3, 10))

    assert plan.granularity == "rok"
    assert [b.label for b in plan.batches] == ["2021", "2022", "2023"]
    assert plan.batches[0].od == date(2021, 6, 15)  # etykieta z okresu, granice z zapytania
    assert plan.batches[-1].do == date(2023, 3, 10)


def test_an_empty_range_is_refused_instead_of_producing_zero_batches() -> None:
    plan_input = criteria(wojewodztwo="mazowieckie", data_od=date(2024, 1, 1))

    with pytest.raises(ValueError, match="zakres dat jest pusty"):
        plan_batches(plan_input, 100, today=date(2023, 1, 1), threshold=THRESHOLD)


# ----------------------------------------------------------------------------- refine


def batch_of(granularity: Granularity, od: date, do: date) -> Batch:
    return Batch(
        criteria=criteria(wojewodztwo="mazowieckie", data_od=od, data_do=do),
        label=f"{od}–{do}",
        od=od,
        do=do,
        granularity=granularity,
    )


@pytest.mark.parametrize(
    ("granularity", "od", "do", "expected_children"),
    [
        ("dekada", date(2020, 1, 1), date(2029, 12, 31), 10),
        ("rok", date(2021, 1, 1), date(2021, 12, 31), 4),
        ("kwartal", date(2021, 1, 1), date(2021, 3, 31), 3),
    ],
    ids=["dekada_na_lata", "rok_na_kwartaly", "kwartal_na_miesiace"],
)
def test_refine_splits_one_batch_exactly_one_level_finer(
    granularity: Granularity, od: date, do: date, expected_children: int
) -> None:
    coarser = batch_of(granularity, od, do)

    children = refine(coarser)

    assert len(children) == expected_children
    assert children[0].od == coarser.od and children[-1].do == coarser.do
    for earlier, later in zip(children, children[1:], strict=False):
        assert earlier.do + DAY == later.od


def test_refine_refuses_to_go_below_a_month() -> None:
    """Dno podziału: dalej program musi poprosić o zawężenie kryteriów, nie kroić dni."""
    plan = plan_for(6_000_000)
    assert plan.granularity == "miesiac"

    assert refine(plan.batches[0]) == ()
