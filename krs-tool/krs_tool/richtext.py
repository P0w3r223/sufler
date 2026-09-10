"""Neutralizator kanału terminalowego i jedyne miejsce, które buduje konsolę.

Skopiowany z `ceidg-tool/ceidg_tool/richtext.py` (kopia z 2026-09-10); zmienione źródło
`mask_tokens`.

Reguła granic 6: napis spoza programu staje się drukowalny na terminalu wyłącznie przez
`safe`. Powód jest zmierzony, nie teoretyczny — w `ceidg-tool` nazwa firmy zawierająca `[/b]`
kończyła program wyjątkiem spoza taksonomii, `[link=…]` robiła z rekordu klikalny odnośnik na
obcy adres, a sekwencja ESC sterowała terminalem. Nazwa spółki w odpisie KRS pochodzi z tego
samego rodzaju źródła.

Kanał markdown ma **własny** neutralizator (`marktext.safe_md`, krok 5) i skan sprawdza pary
kanał-neutralizator, nie samą obecność któregoś. Przepuszczenie napisu przez neutralizator
niewłaściwego kanału jest naruszeniem, nie drobnym niedopatrzeniem.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from .safetext import strip_control
from .secrets import mask_tokens


def make_console() -> Console:
    """Konsola programu — bez `file=`.

    `rich` sięga po `sys.stdout` przy każdym zapisie, więc konsola zbudowana bez `file=`
    trafia tam, gdzie akurat wskazuje strumień. Podanie `file=sys.stdout` zamroziłoby
    strumień z chwili importu i przechwytywanie wyjścia w testach CLI przestałoby działać.
    """
    return Console()


def safe(value: str) -> Text:
    """Napis z zewnątrz jako zwykły tekst: bez znaczników, bez znaków sterujących."""
    return Text(strip_control(mask_tokens(value)))


def safe_or_none(value: str | None) -> Text | None:
    """`safe` dla pól opcjonalnych. Pusty tytuł to brak tytułu, a nie pusty napis.

    Osobna funkcja, a nie wyrażenie warunkowe w miejscu użycia: skan rozpoznaje wywołanie
    neutralizatora, a nie `safe(x) if x else None`.
    """
    return safe(value) if value else None
