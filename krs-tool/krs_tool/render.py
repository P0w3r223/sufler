"""Tłumacz modelu widoku na `rich`. Jeden z trzech modułów, które znają tę bibliotekę.

Skopiowany co do kształtu z `ceidg-tool/ceidg_tool/ui/render.py` (kopia z 2026-09-10).

Każda komórka przechodzi przez `richtext.safe`, także dziś, kiedy wszystkie napisy pochodzą
z `texts.py`, czyli z programu. Powód jest przyszły i zmierzony gdzie indziej: gdy w kroku 2
do `Block` trafi nazwa spółki z odpisu, neutralizator ma już tu stać — a nie zostać dopisany
w tej samej zmianie, w której pojawia się wrogie wejście.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from .richtext import safe, safe_or_none
from .texts import Block


def render_block(block: Block, console: Console) -> None:
    """Rysuje blok: tytuł, opcjonalną tabelę, przypisy."""
    if block.headers:
        table = Table(title=safe_or_none(block.title))
        for header in block.headers:
            table.add_column(safe(header))
        for row in block.rows:
            table.add_row(*(safe(cell) for cell in row))
        console.print(table)
    else:
        console.print(safe(block.title))
        for row in block.rows:
            console.print(safe(" | ".join(row)))
    for note in block.notes:
        console.print(safe(note))
