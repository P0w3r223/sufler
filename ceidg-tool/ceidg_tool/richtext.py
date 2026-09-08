"""Jedyny szew z `rich`: konsola programu i neutralizacja tekstu z zewnątrz.

Reguła granic 10 (ADR-0009): napis spoza programu staje się drukowalny wyłącznie przez
`safe`. Wcześniej ta sama krytyczna dla bezpieczeństwa formuła stała w dwóch miejscach
(`ui/render.py` i `console.py`); dwa egzemplarze reguły „tak i tylko tak tekst z rejestru
trafia na ekran" to o jeden za dużo, bo poprawka jednego nie dotyka drugiego.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from .config import mask_tokens
from .safetext import strip_control


def make_console() -> Console:
    """Konsola programu — bez `file=`.

    `rich` sięga po `sys.stdout` przy każdym zapisie, więc konsola zbudowana bez `file=`
    trafia tam, gdzie akurat wskazuje strumień. Podanie `file=sys.stdout` zamroziłoby
    strumień z chwili importu i przechwytywanie wyjścia w testach CLI przestałoby działać.
    """
    return Console()


def safe(value: str) -> Text:
    """Napis z zewnątrz jako zwykły tekst: bez parsowania znaczników, bez znaków sterujących.

    `rich` czyta nawiasy kwadratowe jako znaczniki, więc nazwa firmy z `[/b]` kończyła
    program wyjątkiem `MarkupError` spoza taksonomii `CeidgError`, `[link=…]` robiła
    z rekordu klikalny odnośnik na obcy adres, a sekwencja ESC sterowała terminalem.
    """
    return Text(strip_control(mask_tokens(value)))


def safe_or_none(value: str | None) -> Text | None:
    """`safe` dla pól opcjonalnych. Pusty tytuł to brak tytułu, a nie pusty napis.

    Osobna funkcja, a nie wyrażenie warunkowe w miejscu użycia: skan reguły 10 rozpoznaje
    wywołanie neutralizatora, a nie `safe(x) if x else None` — i lepiej, żeby rozpoznawał
    kształt prosty, niż żeby uczyć go czytać wyrażenia warunkowe.
    """
    return safe(value) if value else None
