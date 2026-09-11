"""Warstwa poleceń.

**Ten moduł nie drukuje niczym i nie układa żadnego zdania** (reguła granic 7). Zdania
mieszkają w `texts.py`, rysowaniem zajmuje się `render.py`. To nie jest porządek dla porządku:
dopóki warstwa poleceń drukuje sama, reguła „każdy napis z zewnątrz przechodzi przez
neutralizator swojego kanału" wymagałaby analizy przepływu danych przez cały pakiet, zamiast
skanu jednego pliku.

Skan sprawdza wszystkie kanały, nie tylko `rich`: w programie na `typer` pierwszym odruchem
jest `typer.echo`, a nie `console.print`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from .clock import SystemClock, utc_iso
from .dziennik.ladunki import Ladunki
from .dziennik.odtworzenie import odtworz as odtworz_ocene
from .dziennik.skroty import skrot_tekstu
from .dziennik.zapis import Dziennik, wpis_z_oceny
from .errors import ConfigError
from .identity import NumerKRS, numer_krs
from .magazyn import domyslny_magazyn
from .odpis.zrodlo import OdpisZPliku
from .raport.markdown import raport_markdown
from .raport.texts import zbuduj_raport
from .render import render_block, render_raport
from .richtext import make_console
from .signals.katalog import Regula, wczytaj_katalog
from .signals.model import Ocena
from .signals.ocena import ocen_odpis
from .texts import (
    NAZWA,
    Block,
    karta_podmiotu,
    katalog_sygnalow,
    ocena_ryzyka,
    pierwszy_ekran,
    podglad_czyszczenia,
    wyczyszczono_ladunki,
    wynik_odtworzenia,
    zapisano_ocene,
)

app = typer.Typer(
    name=NAZWA,
    help="Raport o ryzyku spółki z KRS na podstawie odpisu dostarczonego przez operatora.",
    no_args_is_help=False,
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Bez polecenia pokazuje ekran powitalny z granicami zakresu."""
    if ctx.invoked_subcommand is not None:
        return
    render_block(pierwszy_ekran(), make_console())


@app.command("pokaz")
def pokaz(
    plik: Annotated[Path, typer.Option("--plik", help="Odpis zapisany wcześniej do pliku.")],
    krs: Annotated[
        str | None,
        typer.Option("--krs", help="Numer KRS, gdy odpis go nie niesie."),
    ] = None,
) -> None:
    """Karta podmiotu odczytana z odpisu — bez oceny i bez zarzutu."""
    numer: NumerKRS | None = numer_krs(krs) if krs else None
    odpis = OdpisZPliku(plik).pobierz(numer)
    render_block(karta_podmiotu(odpis), make_console())


@app.command("katalog")
def katalog() -> None:
    """Katalog reguł sygnałowych do przeglądu — kody, poziomy, podstawy prawne."""
    render_block(katalog_sygnalow(wczytaj_katalog()), make_console())


@app.command("ocen")
def ocen(
    plik: Annotated[Path, typer.Option("--plik", help="Odpis zapisany wcześniej do pliku.")],
    krs: Annotated[
        str | None,
        typer.Option("--krs", help="Numer KRS, gdy odpis go nie niesie."),
    ] = None,
) -> None:
    """Werdykt każdej reguły katalogu wobec tego odpisu — sygnał, wykluczenie albo nieustalone."""
    numer: NumerKRS | None = numer_krs(krs) if krs else None
    odpis = OdpisZPliku(plik).pobierz(numer)
    render_block(ocena_ryzyka(ocen_odpis(odpis, wczytaj_katalog())), make_console())


@app.command("raport")
def raport(
    plik: Annotated[Path, typer.Option("--plik", help="Odpis zapisany wcześniej do pliku.")],
    krs: Annotated[
        str | None,
        typer.Option("--krs", help="Numer KRS, gdy odpis go nie niesie."),
    ] = None,
    markdown: Annotated[
        Path | None,
        typer.Option("--markdown", help="Zapisz raport także jako dokument markdown."),
    ] = None,
    magazyn: Annotated[
        Path | None,
        typer.Option("--magazyn", help="Katalog dziennika i ładunków."),
    ] = None,
    bez_dziennika: Annotated[
        bool,
        typer.Option("--bez-dziennika", help="Nie zapisuj oceny ani kopii odpisu na dysk."),
    ] = False,
) -> None:
    """Pełny raport: sygnały z podstawą i cytatem, nierozstrzygnięte, czego narzędzie nie mówi."""
    numer: NumerKRS | None = numer_krs(krs) if krs else None
    zrodlo = OdpisZPliku(plik)
    odpis = zrodlo.pobierz(numer)
    reguly = wczytaj_katalog()
    ocena = ocen_odpis(odpis, reguly)
    dokument = zbuduj_raport(ocena, odpis)
    konsola = make_console()
    render_raport(dokument, konsola)
    if markdown is not None:
        _zapisz_markdown(markdown, raport_markdown(dokument))
    if not bez_dziennika:
        render_block(_zapisz_w_dzienniku(zrodlo.tresc, ocena, reguly, magazyn), konsola)


def _zapisz_markdown(sciezka: Path, dokument: str) -> None:
    """Zapis pliku wskazanego przez operatora — z błędem z taksonomii, nie ze śladem stosu.

    Literówka w ścieżce jest stanem przewidzianym: `--markdown raporty/x.md` przy nieistniejącym
    katalogu to najzwyklejsza pomyłka, a nie usterka narzędzia.
    """
    try:
        sciezka.write_text(dokument, encoding="utf-8")
    except OSError as blad:
        raise ConfigError(f"Nie da się zapisać raportu w {sciezka}: {blad}") from blad


def _zapisz_w_dzienniku(
    tresc: str, ocena: Ocena, reguly: tuple[Regula, ...], magazyn: Path | None
) -> Block:
    """Linia w dzienniku i kopia odpisu obok niej — w tej kolejności.

    Ładunek zapisujemy PRZED linią dziennika: wpis wskazujący na ładunek, którego nie ma, jest
    gorszy niż ładunek, o którym nie wie dziennik. Pierwszy kłamie przy odtwarzaniu, drugi jest
    tylko śmieciem do sprzątnięcia.

    Treść przychodzi z **tego samego odczytu**, z którego powstała ocena, a nie z drugiego
    otwarcia pliku — inaczej skrót materiału opisywałby plik z chwili zapisu dziennika,
    a werdykty plik sprzed chwili.
    """
    katalog = magazyn if magazyn is not None else domyslny_magazyn()
    skrot = skrot_tekstu(tresc)
    wpis = wpis_z_oceny(ocena, utc_iso(SystemClock().wall()), skrot, reguly)
    sciezka_ladunku = Ladunki(katalog).zapisz(wpis.ocena_id, tresc)
    dziennik = Dziennik(katalog)
    dziennik.dopisz(wpis)
    return zapisano_ocene(wpis, dziennik.sciezka, sciezka_ladunku)


@app.command("odtworz")
def odtworz(
    ocena_id: Annotated[str, typer.Option("--ocena-id", help="Identyfikator z dziennika.")],
    magazyn: Annotated[
        Path | None,
        typer.Option("--magazyn", help="Katalog dziennika i ładunków."),
    ] = None,
) -> None:
    """Przelicza zapisaną ocenę z zachowanego ładunku i mówi, czy wyszło to samo."""
    katalog = magazyn if magazyn is not None else domyslny_magazyn()
    wynik = odtworz_ocene(katalog, ocena_id, wczytaj_katalog())
    render_block(wynik_odtworzenia(wynik), make_console())


@app.command("wyczysc-ladunki")
def wyczysc_ladunki(
    potwierdzam: Annotated[
        bool,
        typer.Option("--potwierdzam", help="Usuń naprawdę. Bez tego polecenie tylko pokazuje."),
    ] = False,
    magazyn: Annotated[
        Path | None,
        typer.Option("--magazyn", help="Katalog dziennika i ładunków."),
    ] = None,
) -> None:
    """Usuwa kopie odpisów z retencji. Dziennika nie dotyka, odtwarzanie przestaje działać."""
    ladunki = Ladunki(magazyn if magazyn is not None else domyslny_magazyn())
    konsola = make_console()
    if not potwierdzam:
        render_block(podglad_czyszczenia(ladunki.lista()), konsola)
        return
    render_block(wyczyszczono_ladunki(ladunki.wyczysc()), konsola)
