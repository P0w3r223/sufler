"""Rysowanie modeli widoku przez `rich`. Jedyna wiedza o wyglądzie, zero decyzji.

§B uzupelnienie-01.md dotyczy także ekranu: nazwa firmy z rejestru trafia tu jako `rich.Text`,
nigdy jako znaczniki. Neutralizacja siedzi w `richtext.safe` (reguła granic 10) — tutaj
zostaje samo rysowanie.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Column, Table

from ..richtext import make_console, safe, safe_or_none
from .texts import Block


class ConsoleView:
    """Widok konsolowy: bloki z `texts` na ekran, komunikaty przez filtr maskujący token."""

    def __init__(self, console: Console | None = None) -> None:
        # `stderr=True` także w zapasowej: niezmiennikiem programu jest „wszystko, co
        # czyta człowiek, idzie na stderr" (ADR-0024, decyzja 2), więc gałąź awaryjna nie może
        # go łamać. Dziś `cli` zawsze podaje konsolę, ale domyślna, która trafia gdzie indziej
        # niż reszta, jest defektem czekającym na pierwsze wywołanie bez argumentu.
        self.console = console or make_console(stderr=True)

    def block(self, block: Block) -> None:
        title = safe_or_none(block.title)
        if block.headers:
            headers = [Column(header=safe(h)) for h in block.headers]
            table = Table(*headers, title=title, title_justify="left")
            for row in block.rows:
                table.add_row(*(safe(cell) for cell in row))
        else:
            table = Table(title=title, show_header=False, title_justify="left")
            table.add_column("klucz", style="bold")
            table.add_column("wartość", overflow="fold")
            for row in block.rows:
                table.add_row(*(safe(cell) for cell in row))
        if block.rows:
            self.console.print(table)
        elif block.title:
            self.console.print(safe(block.title), style="bold")
        for note in block.notes:
            self.console.print(safe(note))

    def message(self, text: str) -> None:
        self.console.print(safe(text))

    def warning(self, text: str) -> None:
        self.console.print("[yellow]Uwaga:[/yellow]", safe(text))

    def error(self, text: str) -> None:
        self.console.print("[red]Błąd:[/red]", safe(text))
