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

from .identity import NumerKRS, numer_krs
from .odpis.zrodlo import OdpisZPliku
from .render import render_block
from .richtext import make_console
from .signals.katalog import wczytaj_katalog
from .texts import NAZWA, karta_podmiotu, katalog_sygnalow, pierwszy_ekran

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
