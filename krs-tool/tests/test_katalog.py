"""Strażnik katalogu reguł: odmawia, zamiast uzupełniać domyślnością.

Reguła niekompletna wygląda identycznie jak kompletna i produkuje oskarżenie tam, gdzie prawo
przewiduje wyjątek. Dlatego każdy brak kończy się odmową wczytania, a każda odmowa ma tu swój
test z zaszczepionym brakiem.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from krs_tool.errors import ConfigError
from krs_tool.signals.katalog import (
    PRZESLANKI_BRAKU_DOKUMENTU,
    Poziom,
    Rodzaj,
    wczytaj_katalog,
)

REGULA_BRAKU = "brak_wpisu_o_sprawozdaniu"


def _wzorzec() -> dict[str, Any]:
    zrodlo = Path(__file__).resolve().parent.parent / "krs_tool" / "signals" / "reguly"
    return dict(yaml.safe_load((zrodlo / "sprawozdania.yaml").read_text(encoding="utf-8")))


def _zasiej(tmp_path: Path, zmiana: Any) -> Path:
    dane = _wzorzec()
    zmiana(dane["reguly"][0])
    (tmp_path / "zasiane.yaml").write_text(
        yaml.safe_dump(dane, allow_unicode=True), encoding="utf-8"
    )
    return tmp_path


def test_prawdziwy_katalog_sie_wczytuje() -> None:
    reguly = wczytaj_katalog()

    assert reguly
    assert all(r.podstawa_prawna for r in reguly)


def test_kazda_regula_ma_zywotnosc() -> None:
    """`zywotnosc` nie ma wartości domyślnej: brak informacji to nie to samo co „bezterminowo"."""
    assert all(r.zywotnosc for r in wczytaj_katalog())


def test_regula_braku_sprawozdania_nie_moze_dzis_wystrzelic() -> None:
    """Stan wymagany przez tabelę bramek, nie usterka — i widoczny w modelu, nie w komentarzu."""
    (regula,) = [r for r in wczytaj_katalog() if r.kod == REGULA_BRAKU]

    assert regula.rodzaj is Rodzaj.BRAK_DOKUMENTU
    assert regula.moze_wystrzelic is False
    nierozstrzygalne = {p.kod for p in regula.przeslanki_wykluczajace if not p.ustalalna}
    assert nierozstrzygalne == {
        "zawieszenie_caloroczne",
        "dzialalnosc_w_spadku",
        "oswiadczenie_art_70a",
    }


def test_termin_biegnie_od_dnia_bilansowego() -> None:
    (regula,) = [r for r in wczytaj_katalog() if r.kod == REGULA_BRAKU]

    assert regula.termin is not None
    assert regula.termin.od == "dzien_bilansowy"


def test_poziom_nie_ma_czlonu_o_spoznieniu() -> None:
    """Połowa mechanizmu zakazu oskarżenia: nie ma wartości, która mogłaby je unieść."""
    nazwy = " ".join(czlon.name.lower() for czlon in Poziom)

    # `TERMINALNY` znaczy „schylkowy", nie „po terminie" — sprawdzamy rdzenie oskarzenia,
    # a nie samo slowo „termin", bo pierwsza wersja tego testu wywalala sie na poprawnej nazwie.
    for rdzen in ("po_terminie", "spozn", "opozn", "nieterminow"):
        assert rdzen not in nazwy


def test_loader_odmawia_przy_pieciu_przeslankach(tmp_path: Path) -> None:
    def usun_jedna(regula: dict[str, Any]) -> None:
        regula["przeslanki_wykluczajace"].pop()

    with pytest.raises(ConfigError) as blad:
        wczytaj_katalog(_zasiej(tmp_path, usun_jedna))

    assert "zamrożoną szóstkę" in str(blad.value)


def test_loader_odmawia_przy_innym_punkcie_odniesienia(tmp_path: Path) -> None:
    """„31 grudnia" jest nieprawdą dla każdego, kto ma rok obrotowy niekalendarzowy."""

    def podmien_punkt(regula: dict[str, Any]) -> None:
        regula["termin"]["od"] = "staly_31_grudnia"

    with pytest.raises(ConfigError) as blad:
        wczytaj_katalog(_zasiej(tmp_path, podmien_punkt))

    assert "dzien_bilansowy" in str(blad.value)


def test_loader_odmawia_bez_zywotnosci(tmp_path: Path) -> None:
    def usun_zywotnosc(regula: dict[str, Any]) -> None:
        del regula["zywotnosc"]

    with pytest.raises(ConfigError):
        wczytaj_katalog(_zasiej(tmp_path, usun_zywotnosc))


def test_loader_odmawia_bez_deklaracji_potwierdzenia_podstawy(tmp_path: Path) -> None:
    """Niezweryfikowany przepis nie może wyglądać jak zweryfikowany."""

    def usun_flage(regula: dict[str, Any]) -> None:
        del regula["podstawa_potwierdzona"]

    with pytest.raises(ConfigError) as blad:
        wczytaj_katalog(_zasiej(tmp_path, usun_flage))

    assert "podstawa_potwierdzona" in str(blad.value)


def test_loader_odmawia_przy_nieznanym_zrodle_ustalenia(tmp_path: Path) -> None:
    def podmien_zrodlo(regula: dict[str, Any]) -> None:
        regula["przeslanki_wykluczajace"][0]["ustalane_z"] = ["dzial99"]

    with pytest.raises(ConfigError):
        wczytaj_katalog(_zasiej(tmp_path, podmien_zrodlo))


def test_loader_odmawia_wczytania_reguly_zablokowanej(tmp_path: Path) -> None:
    """Sygnał, który wygląda na oczywisty, a byłby dziś błędem na masową skalę."""
    dane = _wzorzec()
    regula = copy.deepcopy(dane["reguly"][0])
    regula["kod"] = "brak_sprawozdawczosci_zrownowazonego_rozwoju"
    dane["reguly"] = [regula]
    (tmp_path / "zasiane.yaml").write_text(
        yaml.safe_dump(dane, allow_unicode=True), encoding="utf-8"
    )

    with pytest.raises(ConfigError) as blad:
        wczytaj_katalog(tmp_path)

    assert "zablokowana" in str(blad.value)


def test_zamrozona_szostka_ma_dokladnie_szesc_pozycji() -> None:
    assert len(PRZESLANKI_BRAKU_DOKUMENTU) == len(set(PRZESLANKI_BRAKU_DOKUMENTU))
    assert len(PRZESLANKI_BRAKU_DOKUMENTU) == 6
