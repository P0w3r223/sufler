"""Anonimizator zmienia wartości osobowe i nie rusza kształtu.

To jest test, którego brak kosztował `ceidg-tool` całą rundę: tamtejszy anonimizator
podnosił identyfikatory do wielkich liter, więc suita offline utrwalała brak dokładnie tej
własności, która wywróciła produkcję. Lista `WLASNOSCI_ZACHOWYWANE` w skrypcie jest tu
wejściem, a nie komentarzem — zbiory kodów muszą się zgadzać w obie strony.
"""

from __future__ import annotations

from typing import Any

import pytest

from krs_tool.anonimizacja import (
    WLASNOSCI_ZACHOWYWANE,
    ZASTEPCZA_NAZWA,
    ZASTEPCZE_NAZWISKO,
    ZASTEPCZY_PESEL,
    anonimizuj,
)
from tests.budowniczy import OKRES_KROPKOWY, OKRES_SLOWNY, wzmianka, zbuduj_odpis


def _wzmianki(wynik: Any) -> list[dict[str, str]]:
    blok = wynik["odpis"]["dane"]["dzial3"]["wzmiankiOZlozonychDokumentach"]
    return list(blok["wzmiankaOZlozeniuRocznegoSprawozdaniaFinansowego"])


def test_oba_zapisy_okresu() -> None:
    surowe = zbuduj_odpis(
        sprawozdania=(
            wzmianka("15.07.2024", OKRES_KROPKOWY),
            wzmianka("30.06.2001", OKRES_SLOWNY),
        )
    )

    zapisy = [w["zaOkresOdDo"] for w in _wzmianki(anonimizuj(surowe))]

    assert zapisy == [OKRES_KROPKOWY, OKRES_SLOWNY]


def test_format_daty_zlozenia() -> None:
    surowe = zbuduj_odpis(sprawozdania=(wzmianka("15.07.2024", OKRES_KROPKOWY),))

    assert _wzmianki(anonimizuj(surowe))[0]["dataZlozenia"] == "15.07.2024"


def test_obecnosc_kluczy() -> None:
    """Nie dopisujemy klucza, którego nie było, i nie usuwamy tego, który był."""
    surowe = zbuduj_odpis(regon=None, dzien_konczacy_rok=None, bez_dzialu=5)

    wynik = anonimizuj(surowe)

    dane = wynik["odpis"]["dane"]
    assert "regon" not in dane["dzial1"]["danePodmiotu"]["identyfikatory"]
    assert "informacjaODniuKonczacymRokObrotowy" not in dane["dzial3"]
    assert "dzial5" not in dane
    assert "dzial4" in dane


def test_pustka_dzialow() -> None:
    surowe = zbuduj_odpis(dzial4=None, dzial6={"cos": 1})

    dane = anonimizuj(surowe)["odpis"]["dane"]

    assert dane["dzial4"] is None
    assert dane["dzial6"] == {"cos": 1}


def test_wielkosc_liter() -> None:
    """Wartości spoza pól osobowych wracają znak w znak — bez podnoszenia do wielkich liter."""
    surowe = zbuduj_odpis(forma_prawna="Spółka z o.o. — wariant MiXeD")

    wynik = anonimizuj(surowe)

    assert wynik["odpis"]["dane"]["dzial1"]["danePodmiotu"]["formaPrawna"] == (
        "Spółka z o.o. — wariant MiXeD"
    )


def test_dane_osobowe_znikaja() -> None:
    wynik = anonimizuj(zbuduj_odpis())

    osoba = wynik["odpis"]["dane"]["dzial2"]["reprezentacja"][0]
    assert osoba["nazwisko"] == ZASTEPCZE_NAZWISKO
    assert osoba["pesel"] == ZASTEPCZY_PESEL
    assert wynik["odpis"]["dane"]["dzial1"]["danePodmiotu"]["nazwa"] == ZASTEPCZA_NAZWA


SPRAWDZONE = {
    "oba_zapisy_okresu": test_oba_zapisy_okresu,
    "format_daty_zlozenia": test_format_daty_zlozenia,
    "obecnosc_kluczy": test_obecnosc_kluczy,
    "pustka_dzialow": test_pustka_dzialow,
    "wielkosc_liter": test_wielkosc_liter,
}


def test_kazda_deklarowana_wlasnosc_ma_sprawdzenie() -> None:
    """Własność dopisana do skryptu bez testu zapala bramkę, i odwrotnie."""
    assert set(SPRAWDZONE) == set(WLASNOSCI_ZACHOWYWANE)


@pytest.mark.parametrize("kod", sorted(SPRAWDZONE))
def test_kod_wlasnosci_nie_jest_pusty(kod: str) -> None:
    assert kod.strip()
