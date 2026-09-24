"""Koperta wyniku — wartość czysta i jej zamiana na słownik (ADR-0024).

Cały plik działa bez sieci, bez bazy i bez terminala, i to nie jest wygoda testu: to jest
własność, o którą w tym module chodzi. Ekran i koperta mają być dwoma renderowaniami tych
samych wartości, więc jedno nie może wymagać warstwy, której drugie nie wymaga.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args

import pytest

from ceidg_tool.criteria import Criteria
from ceidg_tool.errors import AuthError, ResumableError
from ceidg_tool.ui.wynik import (
    KLUCZE_WSPOLNE,
    KOD_PUSTO,
    WERSJA_KOPERTY,
    Blad,
    Status,
    Uwaga,
    Wynik,
    kod_wyjscia,
)

MODUL = Path(__file__).resolve().parents[1] / "ceidg_tool" / "ui" / "wynik.py"


# ----------------------------------------------------------------------------- czystość


def test_the_envelope_module_imports_nothing_from_the_layer_it_describes() -> None:
    """Pułapka, której skan reguły 6 **nie** widzi — i dlatego ten test istnieje osobno.

    Skan patrzy na korzenie importów (`httpx`, `sqlite3`, `rich`, `openpyxl`). `from ..pipeline
    import ExportSummary` przeszedłby przezeń bez szmeru, bo korzeniem jest `ceidg_tool` —
    a znosiłby dokładnie to, po co reguła 6 istnieje: koperta dałaby się zbudować wyłącznie
    tam, gdzie da się zbudować `pipeline`, czyli tam, gdzie jest sieć i baza.

    Różnica między regułą wymuszoną a regułą zgłaszaną jako wymuszona.
    """
    drzewo = ast.parse(MODUL.read_text(encoding="utf-8"))
    swoje = {
        wezel.module
        for wezel in ast.walk(drzewo)
        if isinstance(wezel, ast.ImportFrom) and wezel.level > 0
    }

    assert swoje == {"criteria"}


# ----------------------------------------------------------------------------- kod wyjścia


@pytest.mark.parametrize(
    ("status", "oczekiwany"),
    [
        ("ok", 0),
        ("przerwano", 0),
        ("brak_trafien", KOD_PUSTO),
        ("nic_do_zrobienia", KOD_PUSTO),
    ],
)
def test_exit_code_follows_decision_three(status: Status, oczekiwany: int) -> None:
    """Tabela z decyzji 3 wprost. Wypisana ręcznie, bo powtórzenie `match` nie sprawdza go."""
    assert kod_wyjscia(status) == oczekiwany


def test_zero_hits_and_a_cancelled_run_are_different_facts() -> None:
    """Dwa różne zakończenia, dwa różne kody — i to jest cały powód istnienia kodu 4.

    Pod `--tak` pytanie o poszerzenie przy zerze rozstrzyga się bezpieczną domyślną „wyjdź"
    (ADR-0017), więc harmonogram kończy tam, gdzie operator by dopytał. Zlanie tego
    z „przeczytałem tabelę kosztów i zrezygnowałem" odbiera wołającemu jedyną informację,
    której nie da się odzyskać z niczego innego.
    """
    assert kod_wyjscia("brak_trafien") != kod_wyjscia("przerwano")


def test_an_error_takes_its_code_from_the_existing_taxonomy() -> None:
    """Taksonomia `errors.py` zostaje nietknięta — koperta jej nie przepisuje."""
    assert kod_wyjscia("blad", AuthError("x").exit_code) == 3
    assert kod_wyjscia("blad", ResumableError("x").exit_code) == 2


def test_an_error_without_a_code_is_refused_rather_than_guessed() -> None:
    """Zgadnięta jedynka wyglądałaby jak „nieodwracalny" i bywałaby nieprawdą."""
    with pytest.raises(ValueError, match="taksonomii"):
        kod_wyjscia("blad")


def test_every_status_has_an_exit_code() -> None:
    """Kompletność nad zamkniętym zbiorem — po stronie testu, nie tylko mypy.

    mypy pilnuje jej przez `assert_never`, ale dopiero po dopisaniu wariantu; ten test mówi
    to samo po przeczytaniu samego typu, więc zobaczy też wariant dopisany z kodem 0.
    """
    for status in get_args(Status):
        kod = kod_wyjscia(status, 1)

        assert kod in (0, 1, 2, 3, KOD_PUSTO), status


# ----------------------------------------------------------------------------- niezmienniki


def test_a_status_and_an_error_cannot_disagree() -> None:
    """Koperta „ok" niosąca błąd jest gorsza niż jej brak: wołający rozgałęzia się na statusie."""
    with pytest.raises(ValueError, match="nie zgadza się"):
        Wynik(polecenie="pobierz", status="ok", blad=Blad("AuthError", "brak tokenu", 3))

    with pytest.raises(ValueError, match="nie zgadza się"):
        Wynik(polecenie="pobierz", status="blad")


def test_a_command_cannot_quietly_redefine_a_shared_key() -> None:
    """`dodatki` to pola własne polecenia, nie sposób na podmianę `status` albo `rekordy`."""
    with pytest.raises(ValueError, match="przykrywają"):
        Wynik(polecenie="runy", status="ok", dodatki={"rekordy": 7})


def test_the_shared_keys_are_the_ones_the_envelope_always_writes() -> None:
    """Lista strzegąca `dodatki` ma opisywać kopertę, a nie własne wyobrażenie o niej."""
    pelna = Wynik(
        polecenie="pobierz",
        status="ok",
        srodowisko="test",
        kryteria=Criteria(wojewodztwo=("wielkopolskie",)),
        run_ids=("r1",),
        rekordy=5,
        pliki=(Path("a.xlsx"),),
        uwagi=(Uwaga("cokolwiek"),),
    ).koperta()

    assert set(pelna) == KLUCZE_WSPOLNE - {"blad"}


# ----------------------------------------------------------------------------- koperta


def test_the_common_fields_are_there_even_when_nothing_happened() -> None:
    """Wołający czyta siedem pól bez sprawdzania, czy są. Zero żądań to fakt, nie brak danych."""
    koperta = Wynik(polecenie="szukaj-pkd", status="ok").koperta()

    assert koperta == {
        "wersja": WERSJA_KOPERTY,
        "polecenie": "szukaj-pkd",
        "status": "ok",
        "kod_wyjscia": 0,
        "demo": False,
        "zapytania": 0,
        "uwagi": [],
    }


def test_a_command_without_an_environment_does_not_invent_one() -> None:
    """`szukaj-pkd` nie dotyka rejestru. „srodowisko": "test" byłoby zdaniem nieprawdziwym."""
    assert "srodowisko" not in Wynik(polecenie="szukaj-pkd", status="ok").koperta()


def test_paths_become_strings_before_anything_else_touches_them() -> None:
    """Pułapka 2 z ADR-0024: maskowanie w `jsonout` chodzi po napisach.

    `Path`, która dojechałaby tam nietknięta, ominęłaby maskę — a ścieżka pliku bywa jedynym
    polem koperty zawierającym cudzy katalog domowy.
    """
    koperta = Wynik(polecenie="pobierz", status="ok", pliki=(Path("wyniki") / "a.xlsx",)).koperta()

    pliki = koperta["pliki"]
    assert isinstance(pliki, list)
    assert all(isinstance(sciezka, str) for sciezka in pliki)


def test_criteria_are_dumped_in_one_place_and_one_mode() -> None:
    """`mode="json"` tutaj, nie u wołającego — inaczej dwa polecenia dałyby dwa kształty."""
    koperta = Wynik(
        polecenie="pobierz",
        status="ok",
        kryteria=Criteria(wojewodztwo=("wielkopolskie",), pkd=("6210B",)),
    ).koperta()

    assert koperta["kryteria"] == Criteria(
        wojewodztwo=("wielkopolskie",), pkd=("6210B",)
    ).model_dump(mode="json")


def test_an_error_envelope_carries_the_type_and_the_sentence() -> None:
    koperta = Wynik(
        polecenie="pobierz", status="blad", blad=Blad("AuthError", "token odrzucony", 3)
    ).koperta()

    assert koperta["status"] == "blad"
    assert koperta["kod_wyjscia"] == 3
    assert koperta["blad"] == {"typ": "AuthError", "komunikat": "token odrzucony"}


def test_a_note_carries_a_code_for_branching_and_prose_for_reading() -> None:
    """Kod tam, gdzie zamknięty zbiór istnieje; sama proza tam, gdzie jeszcze nie."""
    koperta = Wynik(
        polecenie="pobierz",
        status="ok",
        uwagi=(Uwaga("Raport nie ma linku.", "RAPORT_BEZ_LINKU"), Uwaga("Coś jeszcze.")),
    ).koperta()

    assert koperta["uwagi"] == [
        {"kod": "RAPORT_BEZ_LINKU", "tekst": "Raport nie ma linku."},
        {"kod": "", "tekst": "Coś jeszcze."},
    ]


def test_command_specific_fields_sit_beside_the_shared_ones() -> None:
    koperta = Wynik(polecenie="aktualizuj", status="ok", dodatki={"zmienione": 13, "szczegoly": 8})

    assert koperta.koperta()["zmienione"] == 13
    assert koperta.koperta()["polecenie"] == "aktualizuj"
