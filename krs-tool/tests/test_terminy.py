"""Arytmetyka terminu zastępczego: miesiące, dni i miesiąc, który nie ma tego dnia.

Dzień bilansowy w każdym teście pochodzi z **odczytu zapisu okresu**, a nie z konstruktora typu.
To nie jest ozdoba: `DzienBilansowy` powstaje wyłącznie w `odpis/czytanie.py` (reguła granic 9),
a test, który go sobie wytwarza, sprawdzałby arytmetykę na danych innego rodzaju niż te, które
przyjdą z odpisu.
"""

from __future__ import annotations

from datetime import date

import pytest

from krs_tool.odpis.czytanie import czytaj_okres
from krs_tool.odpis.model import DzienBilansowy
from krs_tool.signals.katalog import Termin
from krs_tool.signals.terminy import (
    MIESIECY_W_ROKU,
    termin_nastepnego_okresu,
    termin_zastepczy,
)

OGRANICZNIK = Termin(rodzaj="zastepczy_ustawowy", od="dzien_bilansowy", miesiecy=6, dni=15)


def dzien_bilansowy(zapis: str) -> DzienBilansowy:
    """Dzień bilansowy tak, jak przychodzi z odpisu — przez czytnik, nigdy obok niego."""
    okres = czytaj_okres(zapis)
    assert okres is not None
    return okres.do


def test_rok_kalendarzowy_konczy_sie_ogranicznikiem_w_lipcu() -> None:
    """Sześć miesięcy i piętnaście dni od 31 grudnia — jedyny przypadek, który zna każdy."""
    termin = termin_zastepczy(dzien_bilansowy("OD 01.01.2023 DO 31.12.2023"), OGRANICZNIK)

    assert termin == date(2024, 7, 15)


def test_rok_obrotowy_niekalendarzowy_liczy_sie_od_swojego_dnia() -> None:
    """Powód, dla którego katalog zna tylko `dzien_bilansowy`: „31 grudnia" jest tu nieprawdą."""
    termin = termin_zastepczy(dzien_bilansowy("OD 01.07.2022 DO 30.06.2023"), OGRANICZNIK)

    assert termin == date(2024, 1, 14)


def test_miesiac_bez_tego_dnia_konczy_termin_dniem_ostatnim() -> None:
    """Zasada z art. 112 kodeksu cywilnego: 31 sierpnia plus sześć miesięcy to koniec lutego.

    Bez tego data byłaby nie do zbudowania, a najprostsza „naprawa" — przelanie nadmiaru na
    marzec — przesuwałaby termin poza dzień wskazany przez ustawę.
    """
    termin = termin_zastepczy(dzien_bilansowy("OD 01.09.2022 DO 31.08.2023"), OGRANICZNIK)

    # 29 lutego, bo 2024 jest przestępny — i dopiero od tego dnia liczy się piętnaście dni.
    assert termin == date(2024, 3, 15)


def test_dzien_bilansowy_z_roku_przestepnego_liczy_sie_normalnie() -> None:
    """29 lutego jest dniem bilansowym jak każdy inny — sześć miesięcy i piętnaście dni."""
    termin = termin_zastepczy(dzien_bilansowy("OD 01.03.2023 DO 29.02.2024"), OGRANICZNIK)

    assert termin == date(2024, 9, 13)


def test_ogranicznik_wypadajacy_29_lutego_przesuwa_sie_na_28() -> None:
    """Jedyne miejsce, w którym przesunięcie o rok trafia w dzień, którego kolejny rok nie ma.

    Dzień bilansowy 14 sierpnia daje ogranicznik 29 lutego 2024. Rok dalej takiego dnia nie ma,
    więc termin kończy się ostatniego lutego — a nie 1 marca, bo termin ustawowy nie przesuwa
    się na korzyść nikogo przez to, że kalendarz jest krótszy.
    """
    termin = termin_zastepczy(dzien_bilansowy("OD 15.08.2022 DO 14.08.2023"), OGRANICZNIK)

    assert termin == date(2024, 2, 29)
    assert termin_nastepnego_okresu(termin) == date(2025, 2, 28)


def test_nastepny_okres_przesuwa_o_rok_obrotowy() -> None:
    termin = termin_zastepczy(dzien_bilansowy("OD 01.01.2023 DO 31.12.2023"), OGRANICZNIK)

    assert termin_nastepnego_okresu(termin) == date(2025, 7, 15)


def test_liczba_miesiecy_w_roku_pochodzi_z_kalendarza() -> None:
    """Reguła granic 10 zabrania tu literału, więc dwanaście bierze się z kalendarza."""
    assert MIESIECY_W_ROKU == len({data.month for data in _pierwsze_dni_roku()})


def _pierwsze_dni_roku() -> list[date]:
    return [date(2024, miesiac, 1) for miesiac in range(1, 13)]


@pytest.mark.parametrize(
    ("zapis", "oczekiwany"),
    [
        ("OD 01.01.2020 DO 31.12.2020", date(2021, 7, 15)),
        ("OD 1 STYCZNIA 2000 ROKU DO 31 GRUDNIA 2000 ROKU", date(2001, 7, 15)),
    ],
)
def test_oba_zapisy_okresu_daja_ten_sam_termin(zapis: str, oczekiwany: date) -> None:
    """Zapis słowny i kropkowy to ten sam okres — termin nie ma prawa się różnić."""
    assert termin_zastepczy(dzien_bilansowy(zapis), OGRANICZNIK) == oczekiwany
