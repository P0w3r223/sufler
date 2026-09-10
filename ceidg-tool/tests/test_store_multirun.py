"""Odczyt z kilku runów naraz i wyszukiwanie runu po odcisku kryteriów.

Te trzy metody obsługują pobranie w partiach (ADR-0008, decyzja 3): `find_run` rozpoznaje
partię już pobraną albo przerwaną, a `iter_records_for_runs` / `count_records_for_runs`
składają partie w jeden eksport. Kluczowa własność to **brak duplikatów**: ta sama firma
może trafić do dwóch partii, gdy między nimi zmieni się jej data rozpoczęcia.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from ceidg_tool.store import Store
from tests.conftest import FakeClock, list_record


@pytest.fixture
def store(tmp_path: Path, clock: FakeClock) -> Iterator[Store]:
    s = Store(tmp_path / "store-test.sqlite", environment="test", clock=clock)
    yield s
    s.close()


def start(
    store: Store,
    run_id: str,
    *,
    criteria_hash: str = "hash-1",
    profile_hash: str = "prof",
    kind: str = "firmy",
) -> str:
    return store.start_run(
        run_id=run_id,
        criteria_json='{"wojewodztwo":["podlaskie"]}',
        criteria_hash=criteria_hash,
        profile_hash=profile_hash,
        mode="lista",
        tool_version="0.1.0",
        cursor_mode="links",
        kind=kind,
    )


def fill(store: Store, run_id: str, ids: list[str]) -> None:
    store.save_page(
        run_id,
        page_index=0,
        records=[list_record(1, id=rec_id) for rec_id in ids],
        next_cursor=None,
    )


# ----------------------------------------------------------------------------- odczyt z partii


def test_records_from_several_runs_are_returned_in_run_order(store: Store) -> None:
    start(store, "run-a")
    start(store, "run-b", criteria_hash="hash-2")
    fill(store, "run-a", ["a1", "a2"])
    fill(store, "run-b", ["b1"])

    ids = [r.id for r in store.iter_records_for_runs(["run-a", "run-b"])]

    assert ids == ["a1", "a2", "b1"]


def test_a_company_present_in_two_runs_is_yielded_once(store: Store) -> None:
    """Firma może zmienić datę rozpoczęcia między partiami i wpaść do obu — eksport ma ją raz."""
    start(store, "run-a")
    start(store, "run-b", criteria_hash="hash-2")
    fill(store, "run-a", ["wspolna", "tylko-a"])
    fill(store, "run-b", ["wspolna", "tylko-b"])

    ids = [r.id for r in store.iter_records_for_runs(["run-a", "run-b"])]

    assert ids == ["wspolna", "tylko-a", "tylko-b"]
    assert len(ids) == len(set(ids))


def test_counting_records_across_runs_counts_each_company_once(store: Store) -> None:
    """Licznik w podsumowaniu musi zgadzać się z liczbą wierszy w arkuszu."""
    start(store, "run-a")
    start(store, "run-b", criteria_hash="hash-2")
    fill(store, "run-a", ["wspolna", "tylko-a"])
    fill(store, "run-b", ["wspolna", "tylko-b"])

    assert store.count_records_for_runs(["run-a", "run-b"]) == 3


def test_the_count_matches_the_number_of_iterated_records(store: Store) -> None:
    """Gdyby licznik i iterator liczyły inaczej, podsumowanie kłamałoby o rozmiarze pliku."""
    start(store, "run-a")
    start(store, "run-b", criteria_hash="hash-2")
    fill(store, "run-a", ["x", "y", "z"])
    fill(store, "run-b", ["y", "q"])

    iterated = list(store.iter_records_for_runs(["run-a", "run-b"]))

    assert store.count_records_for_runs(["run-a", "run-b"]) == len(iterated) == 4


def test_no_runs_yield_nothing_and_count_zero(store: Store) -> None:
    """Pusta lista runów nie może wywrócić zapytania SQL o pustą klauzulę IN."""
    assert list(store.iter_records_for_runs([])) == []
    assert store.count_records_for_runs([]) == 0


def test_a_single_run_behaves_like_the_plain_iterator(store: Store) -> None:
    start(store, "run-a")
    fill(store, "run-a", ["a1", "a2"])

    assert [r.id for r in store.iter_records_for_runs(["run-a"])] == [
        r.id for r in store.iter_run_records("run-a")
    ]


def test_an_empty_run_contributes_nothing(store: Store) -> None:
    start(store, "run-a")
    start(store, "run-pusty", criteria_hash="hash-2")
    fill(store, "run-a", ["a1"])

    assert store.count_records_for_runs(["run-a", "run-pusty"]) == 1


# ----------------------------------------------------------------------------- find_run


def test_find_run_returns_the_run_matching_the_fingerprint_and_status(store: Store) -> None:
    """Tak plan partii rozpoznaje partię już pobraną — po odcisku kryteriów, nie po nazwie."""
    start(store, "run-a", criteria_hash="odcisk-partii")
    store.update_run_status("run-a", "zakonczony")

    found = store.find_run("odcisk-partii", statuses=("zakonczony",))

    assert found is not None and found.run_id == "run-a"


def test_find_run_ignores_a_run_in_another_status(store: Store) -> None:
    """Partia przerwana nie może udawać pobranej — inaczej plan pominąłby niedokończoną pracę."""
    start(store, "run-a", criteria_hash="odcisk-partii")
    store.update_run_status("run-a", "przerwany")

    assert store.find_run("odcisk-partii", statuses=("zakonczony",)) is None
    assert store.find_run("odcisk-partii", statuses=("przerwany", "w_toku")) is not None


def test_find_run_ignores_a_different_fingerprint(store: Store) -> None:
    start(store, "run-a", criteria_hash="odcisk-a")
    store.update_run_status("run-a", "zakonczony")

    assert store.find_run("odcisk-b", statuses=("zakonczony",)) is None


def test_find_run_returns_the_newest_of_several_matches(store: Store) -> None:
    """Powtórzone pobranie tej samej partii zostawia kilka runów — liczy się najnowszy."""
    for run_id in ("run-stary", "run-nowy"):
        start(store, run_id, criteria_hash="odcisk-partii")
        store.update_run_status(run_id, "zakonczony")

    found = store.find_run("odcisk-partii", statuses=("zakonczony",))

    assert found is not None and found.run_id == "run-nowy"


def test_find_run_with_no_statuses_matches_nothing(store: Store) -> None:
    """Pusta lista stanów to zapytanie bez sensu — lepiej `None` niż `IN ()` w SQL."""
    start(store, "run-a", criteria_hash="odcisk-partii")

    assert store.find_run("odcisk-partii", statuses=()) is None


def test_find_run_accepts_several_statuses_at_once(store: Store) -> None:
    """Plan pyta o „przerwany albo w_toku” jednym wywołaniem."""
    start(store, "run-a", criteria_hash="odcisk-partii")  # startuje jako `w_toku`

    found = store.find_run("odcisk-partii", statuses=("przerwany", "w_toku"))

    assert found is not None and found.status == "w_toku"


# ------------------------------------------------- profil i rodzaj runu przy szukaniu partii


def test_a_run_from_another_api_profile_does_not_hide_the_matching_one(store: Store) -> None:
    """Filtr profilu siedzi w zapytaniu, nie za nim: gdyby profil sprawdzać dopiero po
    wybraniu wiersza, nowszy run pod innym dialektem przesłoniłby pasującą partię i cała
    partia byłaby pobierana od nowa."""
    start(store, "stary-A", profile_hash="A")
    store.update_run_status("stary-A", "zakonczony")
    start(store, "nowy-B", profile_hash="B")
    store.update_run_status("nowy-B", "zakonczony")

    found_a = store.find_run("hash-1", statuses=("zakonczony",), profile_hash="A")
    found_b = store.find_run("hash-1", statuses=("zakonczony",), profile_hash="B")
    found_c = store.find_run("hash-1", statuses=("zakonczony",), profile_hash="C")

    assert found_a is not None and found_a.run_id == "stary-A"
    assert found_b is not None and found_b.run_id == "nowy-B"
    assert found_c is None


def test_a_report_run_is_not_mistaken_for_a_fetched_batch(store: Store) -> None:
    """Pobranie z raportu ma ten sam odcisk kryteriów co partia z API, ale jego rekordy nie
    mają identyfikatora wpisu ani szczegółów — wzięte za gotową partię zafałszowałoby wynik."""
    start(store, "z-raportu", kind="raport")
    store.update_run_status("z-raportu", "zakonczony")

    assert store.find_run("hash-1", statuses=("zakonczony",)) is None
    assert store.find_run("hash-1", statuses=("zakonczony",), kind="raport") is not None


def test_without_a_profile_argument_the_query_is_what_it_was_before(store: Store) -> None:
    """Domyślne wywołanie ma zachowywać się jak przed wprowadzeniem filtra profilu."""
    start(store, "run-x", profile_hash="A")
    store.update_run_status("run-x", "zakonczony")

    found = store.find_run("hash-1", statuses=("zakonczony",))

    assert found is not None and found.run_id == "run-x"
