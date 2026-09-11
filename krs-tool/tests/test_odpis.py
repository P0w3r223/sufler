"""Odczyt odpisu: oba zapisy okresu, zapis nieznany, trzy stany działu.

Materiał jest syntetyczny, więc te testy dowodzą **poprawności czytania**, a nie niczego
o rejestrze. Rozdział jest pilnowany osobno (`test_odpis_traits.py`).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from krs_tool.errors import (
    BrakZrodlaPublicznegoError,
    NieznanyKsztaltOdpisuError,
    OdpisNieczytelnyError,
)
from krs_tool.identity import numer_krs
from krs_tool.odpis.czytanie import czytaj_okres, wczytaj_odpis
from krs_tool.odpis.zrodlo import OdpisZPliku, Zrodlo
from tests.budowniczy import (
    OKRES_KROPKOWY,
    OKRES_NIEZNANY,
    OKRES_SLOWNY,
    wzmianka,
    zbuduj_odpis,
)


def test_czyta_zapis_kropkowy() -> None:
    okres = czytaj_okres(OKRES_KROPKOWY)

    assert okres is not None
    assert okres.od == date(2023, 1, 1)
    assert okres.do == date(2023, 12, 31)


def test_czyta_zapis_slowny() -> None:
    """Starsze wzmianki rejestr zapisuje słownie — ten sam okres, inny zapis."""
    okres = czytaj_okres(OKRES_SLOWNY)

    assert okres is not None
    assert okres.od == date(2000, 1, 1)
    assert okres.do == date(2000, 12, 31)


def test_nieznany_zapis_nie_jest_zgadywany() -> None:
    """Trzeci zapis zwraca `None`, a nie wymyślony rok obrotowy."""
    assert czytaj_okres(OKRES_NIEZNANY) is None


def test_wzmianka_nieczytelna_zachowuje_surowy_zapis() -> None:
    surowe = zbuduj_odpis(sprawozdania=(wzmianka("15.07.2020", OKRES_NIEZNANY),))

    odpis = wczytaj_odpis(surowe)

    (nieczytelna,) = odpis.wzmianki_nieczytelne()
    assert nieczytelna.zapis_okresu == OKRES_NIEZNANY
    assert nieczytelna.data_zlozenia == date(2020, 7, 15)


def test_dzial_ma_trzy_stany() -> None:
    """Brak działu w pliku to co innego niż dział pusty — i to co innego niż niepusty."""
    surowe = zbuduj_odpis(dzial4={"zaleglosci": [{"kwota": "1000"}]}, bez_dzialu=5)

    odpis = wczytaj_odpis(surowe)

    czwarty = odpis.dzial(4)
    piaty = odpis.dzial(5)
    szosty = odpis.dzial(6)
    assert czwarty is not None and czwarty.obecny and not czwarty.pusty
    assert piaty is not None and not piaty.obecny
    assert szosty is not None and szosty.obecny and szosty.pusty


def test_znacznik_syntetyczny_jest_odczytywany() -> None:
    assert wczytaj_odpis(zbuduj_odpis()).syntetyczny is True


def test_brak_wymaganego_pola_nazywa_sciezke() -> None:
    surowe = zbuduj_odpis()
    del surowe["odpis"]["naglowekA"]["stanZDnia"]

    with pytest.raises(NieznanyKsztaltOdpisuError) as blad:
        wczytaj_odpis(surowe)

    assert "stanZDnia" in str(blad.value)


def test_brak_numeru_i_brak_podpowiedzi_konczy_sie_wyjasnieniem() -> None:
    surowe = zbuduj_odpis()
    del surowe["odpis"]["naglowekA"]["numerKRS"]

    with pytest.raises(NieznanyKsztaltOdpisuError) as blad:
        wczytaj_odpis(surowe)

    assert "docs/pomiary.md" in str(blad.value)


def test_numer_podany_przez_wolajacego_wystarcza() -> None:
    surowe = zbuduj_odpis()
    del surowe["odpis"]["naglowekA"]["numerKRS"]

    odpis = wczytaj_odpis(surowe, numer=numer_krs("0000123456"))

    assert odpis.numer == "0000123456"


def test_adapter_czyta_plik(tmp_path: Path) -> None:
    plik = tmp_path / "odpis.json"
    plik.write_text(json.dumps(zbuduj_odpis(), ensure_ascii=False), encoding="utf-8")

    odpis = OdpisZPliku(plik).pobierz()

    assert odpis.nazwa.startswith("PRZYKLADOWA")


def test_adapter_odmawia_na_niepoprawnym_pliku(tmp_path: Path) -> None:
    plik = tmp_path / "odpis.json"
    plik.write_text("{to nie jest json", encoding="utf-8")

    with pytest.raises(OdpisNieczytelnyError):
        OdpisZPliku(plik).pobierz()


def test_zrodlo_ma_dokladnie_jeden_czlon() -> None:
    """Drugi człon dokłada adapter sieciowy — a ten czeka na stanowisko ministerstwa.

    Jeżeli ten test zapłonął, to znaczy, że ktoś dokłada `OpenApiKRS`. Zanim to zrobi,
    ma przeczytać `docs/adr/0001` decyzja 2 i upewnić się, że decyzja właściciela się zmieniła.
    """
    assert [czlon.name for czlon in Zrodlo] == ["PLIK_OPERATORA"]


def test_odpis_spoza_rejestru_przedsiebiorcow_jest_odrzucany() -> None:
    """Uspokajający raport o podmiocie spoza zakresu to najgorszy tryb awarii tego produktu.

    Zanim powstała ta bramka, odpis z `rejestr=S` przechodził cały potok i dawał raport
    z dziesięcioma wykluczeniami — czytany jak zaświadczenie, choć reguły katalogu opisują
    przepisy o rejestrze przedsiębiorców, czyli o innym rejestrze niż ten odpis.
    """
    with pytest.raises(BrakZrodlaPublicznegoError) as blad:
        wczytaj_odpis(zbuduj_odpis(rejestr="S"))

    assert "'S'" in str(blad.value)
    assert "Szefa KAS" in str(blad.value)


def test_pusty_rejestr_nie_zamyka_drogi() -> None:
    """Brak wartości to niewiedza, nie przynależność — odmowa byłaby tu zgadywaniem."""
    odpis = wczytaj_odpis(zbuduj_odpis(rejestr=""))

    assert odpis.rejestr == ""
