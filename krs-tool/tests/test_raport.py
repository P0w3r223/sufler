"""Raport: cztery rzeczy przy każdym sygnale, dwa kanały i jeden wrogi napis.

Golden-testy porównują cały dokument markdown **bajt w bajt**. To jest ich sens: redakcja,
która gubi kolumnę z podstawą prawną albo skraca sekcję „czego to narzędzie nie twierdzi",
przechodzi każdy test asercyjny i wywraca dopiero golden — bo golden jest jedynym testem,
który widzi raport tak, jak zobaczy go czytelnik.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from krs_tool.cli import app
from krs_tool.marktext import safe_md
from krs_tool.odpis.czytanie import wczytaj_odpis
from krs_tool.odpis.model import Odpis
from krs_tool.raport.markdown import raport_markdown
from krs_tool.raport.texts import CZEGO_NIE_TWIERDZI, NAGLOWKI_SYGNALOW, Raport, zbuduj_raport
from krs_tool.signals.katalog import wczytaj_katalog
from krs_tool.signals.ocena import ocen_odpis
from tests.budowniczy import OKRES_KROPKOWY, OKRES_NIEZNANY, wzmianka, zbuduj_odpis

GOLDEN = Path(__file__).resolve().parent / "golden"

SPRAWOZDANIE = (wzmianka("10.05.2024", OKRES_KROPKOWY),)
WPIS_DZIALU_4 = {
    "zaleglosciPodatkowe": [{"kwota": "12000"}],
    "wierzyciele": [],
}

# Nazwa spółki, jakiej rejestr nigdy nie wystawi, ale człowiek wpisać może. Każdy jej fragment
# psuje inny kanał: `](…)` robi odnośnik w markdownie, `|` rozbija wiersz tabeli, `[red]` jest
# znacznikiem `rich`, a ESC steruje terminalem.
NAZWA_WROGA = "SPOLKA [zobacz](http://zle.example) | [red]ALFA[/red] \x1b[31m"

PRZYPADKI: dict[str, dict[str, Any]] = {
    "zdrowa": {"sprawozdania": SPRAWOZDANIE},
    "dzial4_niepusty": {"sprawozdania": SPRAWOZDANIE, "dzial4": WPIS_DZIALU_4},
    "okres_nieczytelny": {"sprawozdania": (wzmianka("15.07.2020", OKRES_NIEZNANY),)},
    "nazwa_wroga": {"sprawozdania": SPRAWOZDANIE, "nazwa": NAZWA_WROGA, "dzial4": WPIS_DZIALU_4},
}


def _odpis(**kwargs: Any) -> Odpis:
    return wczytaj_odpis(zbuduj_odpis(**kwargs))


def _raport(**kwargs: Any) -> Raport:
    odpis = _odpis(**kwargs)
    return zbuduj_raport(ocen_odpis(odpis, wczytaj_katalog()), odpis)


def test_kazdy_sygnal_niesie_cztery_rzeczy() -> None:
    """Poziom, podstawa prawna, dzień obserwacji i cytat z odpisu — przy każdym, bez wyjątku."""
    sekcja = _raport(**PRZYPADKI["dzial4_niepusty"]).sekcje[1]

    assert sekcja.headers == NAGLOWKI_SYGNALOW
    assert sekcja.rows
    for wiersz in sekcja.rows:
        assert len(wiersz) == len(NAGLOWKI_SYGNALOW)
        assert all(komorka.strip() for komorka in wiersz)


def test_cytat_jest_przepisany_z_pliku_a_nie_zinterpretowany() -> None:
    """Cytujemy nazwy pól, które plik naprawdę niesie — bez zestawiania ich z czymkolwiek."""
    sekcja = _raport(**PRZYPADKI["dzial4_niepusty"]).sekcje[1]

    (cytat,) = [wiersz[-1] for wiersz in sekcja.rows]
    assert "zaleglosciPodatkowe" in cytat
    assert "wierzyciele" in cytat


def test_dzien_obserwacji_pochodzi_z_odpisu() -> None:
    """ADR-0001 decyzja 6: jedyne „dziś" tego narzędzia to `stanZDnia`."""
    sekcja = _raport(**PRZYPADKI["dzial4_niepusty"], stan_z_dnia="04.02.2026").sekcje[1]

    assert all(wiersz[3] == "2026-02-04" for wiersz in sekcja.rows)


def test_raport_mowi_czego_nie_widzi_zamiast_przemilczec() -> None:
    """Najmocniejszy pojedynczy sygnał w tej dziedzinie jest poza zasięgiem i raport to pisze."""
    tresc = _raport(**PRZYPADKI["zdrowa"]).as_text()

    assert "rejestru dłużników niewypłacalnych" in tresc
    assert all(zdanie in tresc for zdanie in CZEGO_NIE_TWIERDZI)


def test_brak_sygnalow_nie_czyta_sie_jak_zaswiadczenie() -> None:
    """„Nic nie znaleziono" o spółce jest zdaniem groźnym i ma być opatrzone."""
    tresc = _raport(**PRZYPADKI["zdrowa"]).as_text()

    assert "To nie znaczy, że podmiot jest bez ryzyka" in tresc


def test_nierozstrzygniete_mowi_kto_je_zamyka() -> None:
    tresc = _raport(**PRZYPADKI["zdrowa"]).as_text()

    assert "pomiar albo stanowisko ministerstwa" in tresc
    assert "zmiana w tym narzędziu" in tresc


# --------------------------------------------------------------------------------------
# Wrogi napis w obu kanałach
# --------------------------------------------------------------------------------------


def test_markdown_gasi_odnosnik_i_kreske_tabeli() -> None:
    """`](http://…)` robi odnośnik, a `|` przesuwa kolumny — obie rzeczy są zgaszone."""
    dokument = raport_markdown(_raport(**PRZYPADKI["nazwa_wroga"]))

    assert "](http://zle.example)" not in dokument
    assert "\\|" in dokument
    assert "\x1b" not in dokument


def test_markdown_zachowuje_ksztalt_tabeli_mimo_wrogiej_nazwy() -> None:
    """Kreski wokół komórek są nasze; te z nazwy spółki nie mają prawa dołożyć kolumny."""
    dokument = raport_markdown(_raport(**PRZYPADKI["nazwa_wroga"]))

    wiersze = [linia for linia in dokument.splitlines() if linia.startswith("| ")]
    kolumny = {linia.count(" | ") for linia in wiersze if "\\|" in linia}
    assert kolumny <= {1, 4}


def test_terminal_pokazuje_znacznik_rich_jako_tekst() -> None:
    """Gdyby `[red]` poszło na terminal jako znacznik, w wyjściu nie byłoby go widać."""
    plik = GOLDEN / "chwilowy.json"
    try:
        plik.write_text(
            json.dumps(zbuduj_odpis(**PRZYPADKI["nazwa_wroga"]), ensure_ascii=False),
            encoding="utf-8",
        )
        wynik = CliRunner().invoke(app, ["raport", "--plik", str(plik)])
    finally:
        plik.unlink(missing_ok=True)

    assert wynik.exit_code == 0
    assert "\x1b[31m" not in wynik.output


def test_markdown_zdejmuje_sterowanie_kierunkiem_pisma() -> None:
    """Nazwa wyświetlana inaczej, niż jest zapisana, w dokumencie przesyłanym dalej jako raport.

    `strip_control` tego nie łapie, bo te znaki leżą powyżej U+0020. Kanał terminalowy dzieli
    tamten neutralizator z `ceidg-tool` i naprawa tam jest decyzją właściciela (ADR-0023
    decyzja 1) — tutaj domykamy własny moduł.
    """
    zgaszone = safe_md("ALFA‮BETA⁦")

    assert zgaszone == "ALFABETA"


def test_neutralizator_markdownu_nie_zjada_tresci() -> None:
    """Gaszenie składni nie może zmieniać słów — to raport o konkretnej spółce."""
    zgaszone = safe_md("ALFA | BETA")

    assert "ALFA" in zgaszone
    assert "BETA" in zgaszone


# --------------------------------------------------------------------------------------
# Golden
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("nazwa", sorted(PRZYPADKI))
def test_golden_raport(nazwa: str) -> None:
    """Cały dokument, bajt w bajt. Przy zmianie treści: obejrzyj różnicę, zanim ją zatwierdzisz."""
    dokument = raport_markdown(_raport(**PRZYPADKI[nazwa]))
    wzorzec = GOLDEN / f"raport_{nazwa}.md"
    if not wzorzec.exists():  # pragma: no cover - jednorazowo, przy dopisaniu przypadku
        wzorzec.write_text(dokument, encoding="utf-8")
        pytest.fail(f"Zapisano nowy wzorzec {wzorzec.name} — przeczytaj go i zatwierdź w commicie.")

    assert dokument == wzorzec.read_text(encoding="utf-8")
