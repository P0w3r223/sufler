"""Numer KRS: postać kanoniczna i ostrzeżenie przed numerem, który wygląda na NIP."""

from __future__ import annotations

import pytest

from krs_tool.errors import OdpisNieczytelnyError
from krs_tool.identity import KsztaltNumeru, numer_krs, ocen_ksztalt, wyglada_na_nip


def test_zera_wiodace_sa_znaczace() -> None:
    """`0000028860` to nie `28860` — numer trzymamy jako napis, nigdy jako liczbę."""
    assert numer_krs("0000028860") == "0000028860"


def test_znaki_rozdzielajace_sa_usuwane() -> None:
    assert numer_krs("0000-028-860") == "0000028860"


def test_zla_dlugosc_konczy_sie_wyjasnieniem() -> None:
    with pytest.raises(OdpisNieczytelnyError):
        numer_krs("123")


def test_numer_o_sumie_kontrolnej_nip_jest_rozpoznawany() -> None:
    """NIP i KRS mają po dziesięć cyfr; rozróżnia je wyłącznie suma kontrolna NIP."""
    assert wyglada_na_nip("5252248481") is True

    assert ocen_ksztalt("5252248481") is KsztaltNumeru.WYGLADA_NA_NIP


def test_typowy_numer_krs_nie_udaje_nip() -> None:
    assert ocen_ksztalt("0000028860") is KsztaltNumeru.POPRAWNY


def test_zla_dlugosc_ma_wlasny_werdykt() -> None:
    """Trzy stany, bo operator ma dostać inną wiadomość przy literówce, a inną przy NIP-ie."""
    assert ocen_ksztalt("28860") is KsztaltNumeru.ZLA_DLUGOSC
