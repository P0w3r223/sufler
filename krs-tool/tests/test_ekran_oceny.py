"""Ekran oceny: co operator zobaczy i czego na tym ekranie nie ma.

Przedmiotem odbioru kroku 4 są dwie własności wydruku, obie łatwe do zepsucia redakcją.
**Reguła nieustalona zajmuje tyle samo miejsca co sygnał** — gdyby nieustalone chowało się
w przypisie, raport zaczynałby kłamać przez przemilczenie. I **żaden wiersz nie niesie
zarzutu**, nawet przy najmocniejszym możliwym stanie odpisu.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from krs_tool.cli import app
from krs_tool.odpis.czytanie import wczytaj_odpis
from krs_tool.signals.katalog import PRZESLANKI_BRAKU_DOKUMENTU, wczytaj_katalog
from krs_tool.signals.model import Obserwacja, Powod
from krs_tool.signals.ocena import ocen_odpis
from krs_tool.texts import (
    OPISY_OBSERWACJI,
    OPISY_POWODOW,
    OPISY_PRZESLANEK,
    ZNACZNIK_SYNTETYCZNY,
    ocena_ryzyka,
)
from tests.budowniczy import OKRES_KROPKOWY, wzmianka, zbuduj_odpis

SPRAWOZDANIE = (wzmianka("10.05.2024", OKRES_KROPKOWY),)


def _ekran(**kwargs: object) -> str:
    odpis = wczytaj_odpis(zbuduj_odpis(sprawozdania=SPRAWOZDANIE, **kwargs))  # type: ignore[arg-type]
    return ocena_ryzyka(ocen_odpis(odpis, wczytaj_katalog())).as_text()


def test_ekran_pokazuje_wiersz_dla_kazdej_reguly() -> None:
    blok = ocena_ryzyka(
        ocen_odpis(wczytaj_odpis(zbuduj_odpis(sprawozdania=SPRAWOZDANIE)), wczytaj_katalog())
    )

    assert len(blok.rows) == len(wczytaj_katalog())


def test_kolumna_poziomu_mowi_o_regule_a_nie_o_wyniku() -> None:
    """Samo „terminalny" przy wierszu nieustalonym czyta się jak werdykt — i nim nie jest."""
    blok = ocena_ryzyka(
        ocen_odpis(wczytaj_odpis(zbuduj_odpis(sprawozdania=SPRAWOZDANIE)), wczytaj_katalog())
    )

    assert "poziom reguły" in blok.headers


def test_nieustalone_wymienia_przeslanki_na_wydruku() -> None:
    tresc = _ekran()

    assert "zawieszenie działalności przez cały rok obrotowy" in tresc
    assert "z odpisu tego nie da się ustalić" in tresc
    assert "narzędzie jeszcze nie czyta tej danej z odpisu" in tresc


def test_wydruk_nie_niesie_zarzutu_przy_najmocniejszym_stanie_odpisu() -> None:
    """Dział 4 niepusty to najmocniejsze, co ten produkt umie powiedzieć — i mówi to o wpisie."""
    tresc = _ekran(dzial4={"zaleglosci": [{"pozycja": 1}]})

    assert "nie zawiera zarzutu wobec podmiotu" in tresc
    assert "dział niepusty w odpisie" in tresc
    for zwrot in ("po terminie", "z opóźnieniem", "za późno", "narusza obowiązek"):
        assert zwrot not in tresc


def test_ekran_z_odpisu_syntetycznego_nosi_znacznik() -> None:
    blok = ocena_ryzyka(
        ocen_odpis(wczytaj_odpis(zbuduj_odpis(sprawozdania=SPRAWOZDANIE)), wczytaj_katalog())
    )

    assert ZNACZNIK_SYNTETYCZNY in blok.title
    assert ZNACZNIK_SYNTETYCZNY in blok.notes[0]


def test_liczby_werdyktow_zgadzaja_sie_z_wierszami() -> None:
    """Przypis z licznikami jest pierwszym miejscem, w którym redakcja rozjeżdża się z danymi."""
    odpis = wczytaj_odpis(zbuduj_odpis(sprawozdania=SPRAWOZDANIE, dzial4={"x": [1]}))
    ocena = ocen_odpis(odpis, wczytaj_katalog())

    tresc = ocena_ryzyka(ocena).as_text()

    assert f"Sygnałów: {len(ocena.sygnaly())}." in tresc
    assert f"Nieustalonych: {len(ocena.nieustalone())}." in tresc
    assert f"Wykluczonych: {len(ocena.wykluczone())}." in tresc


def test_polecenie_ocen_dziala_na_pliku(tmp_path: Path) -> None:
    plik = tmp_path / "odpis.json"
    plik.write_text(
        json.dumps(zbuduj_odpis(sprawozdania=SPRAWOZDANIE), ensure_ascii=False), encoding="utf-8"
    )

    wynik = CliRunner().invoke(app, ["ocen", "--plik", str(plik)])

    assert wynik.exit_code == 0
    # Przypis z licznikami, a nie tytuł: w wąskim terminalu tytuł się zawija, więc test na
    # nim sprawdzałby szerokość konsoli zamiast tego, czy polecenie policzyło werdykty.
    assert "Sygnałów: 0." in wynik.output
    assert "Wykluczonych: 9." in wynik.output


def test_kazdy_kod_ma_swoje_zdanie() -> None:
    """Dwie listy tych samych nazw rozjeżdżają się przy pierwszym dopisku.

    Kod bez tłumaczenia nie wywraca programu — degraduje się do surowego identyfikatora na
    ekranie operatora, czyli psuje się po cichu i akurat u tego, kto najmniej może z tym
    zrobić.
    """
    assert set(OPISY_PRZESLANEK) == set(PRZESLANKI_BRAKU_DOKUMENTU)
    assert set(OPISY_OBSERWACJI) == {o.value for o in Obserwacja}
    assert set(OPISY_POWODOW) == {p.value for p in Powod}


def test_zalozenie_jest_widoczne_przy_werdykcie_mocnym() -> None:
    """Wykluczenie liczone z przesuniętego terminu niesie na wydruku swoje założenie."""
    tresc = _ekran(stan_z_dnia="01.03.2025")

    assert "przy założeniu:" in tresc
    assert "rok obrotowy jest tej samej długości" in tresc
