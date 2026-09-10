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

import typer

from .render import render_block
from .richtext import make_console
from .texts import NAZWA, pierwszy_ekran

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
