"""Karta podmiotu: co pokazuje, czego nie obiecuje i jak oznacza materiał wymyślony."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from krs_tool.cli import app
from krs_tool.odpis.czytanie import wczytaj_odpis
from krs_tool.texts import ZNACZNIK_SYNTETYCZNY, karta_podmiotu
from tests.budowniczy import OKRES_KROPKOWY, OKRES_NIEZNANY, wzmianka, zbuduj_odpis


def test_karta_z_odpisu_syntetycznego_nosi_znacznik() -> None:
    """Raport z wymyślonych danych ma być nie do pomylenia z prawdziwym."""
    karta = karta_podmiotu(wczytaj_odpis(zbuduj_odpis()))

    assert ZNACZNIK_SYNTETYCZNY in karta.title
    assert ZNACZNIK_SYNTETYCZNY in karta.notes[0]


def test_karta_nie_obiecuje_oceny() -> None:
    karta = karta_podmiotu(wczytaj_odpis(zbuduj_odpis()))

    assert "Nie zawiera żadnej oceny ani zarzutu." in " ".join(karta.notes)


def test_nieczytelny_okres_jest_zglaszany_a_nie_pomijany() -> None:
    surowe = zbuduj_odpis(
        sprawozdania=(
            wzmianka("15.07.2024", OKRES_KROPKOWY),
            wzmianka("15.07.2020", OKRES_NIEZNANY),
        )
    )

    tresc = karta_podmiotu(wczytaj_odpis(surowe)).as_text()

    assert f"nieczytelny zapis okresu: {OKRES_NIEZNANY}" in tresc
    assert f"za okres {OKRES_KROPKOWY}" in tresc
    assert "Nieczytelnych zapisów okresu: 1" in tresc


def test_karta_rozroznia_trzy_stany_dzialu() -> None:
    surowe = zbuduj_odpis(dzial4={"zaleglosci": [1]}, bez_dzialu=5)

    tresc = karta_podmiotu(wczytaj_odpis(surowe)).as_text()

    assert "dział 4 | niepusty" in tresc
    assert "dział 5 | brak w pliku" in tresc
    assert "dział 6 | pusty" in tresc


def test_polecenie_pokaz_dziala_na_pliku(tmp_path: Path) -> None:
    plik = tmp_path / "odpis.json"
    plik.write_text(json.dumps(zbuduj_odpis(), ensure_ascii=False), encoding="utf-8")

    wynik = CliRunner().invoke(app, ["pokaz", "--plik", str(plik)])

    assert wynik.exit_code == 0
    assert "karta podmiotu" in wynik.output
