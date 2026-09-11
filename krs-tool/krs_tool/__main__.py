"""Wejście `python -m krs_tool` oraz punkt wejścia skryptu `krs-tool`.

Tu mieszka **jedyne** miejsce, które zamienia wyjątek z taksonomii na ekran i kod wyjścia.
Gdyby robiła to warstwa poleceń, każde polecenie miałoby własną obsługę błędu i któreś by ją
kiedyś zgubiło; a gdyby nie robił tego nikt, operator dostawałby ślad stosu w sytuacjach, które
są przewidziane — po wyczyszczeniu retencji albo przy pliku, który nie jest odpisem.
"""

from __future__ import annotations

from .cli import app
from .errors import KrsError
from .render import render_block
from .richtext import make_console
from .texts import blad_operatora


def main() -> None:
    """Uruchamia program i tłumaczy wyjątek taksonomii na komunikat oraz kod wyjścia."""
    try:
        app()
    except KrsError as blad:
        render_block(blad_operatora(blad), make_console())
        raise SystemExit(blad.exit_code) from blad


if __name__ == "__main__":
    main()
