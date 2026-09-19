"""Neutralizacja wrogich napisów z rejestru — moduł czysty, wspólny dla eksportu i ekranu.

§B uzupelnienie-01.md: dane z rejestru traktujemy jako wrogie. Arkusz i CSV chroni prefiks
apostrofu przed wykonaniem formuły, a terminal — usunięcie znaków sterujących (sekwencje
ANSI potrafią wyczyścić ekran albo podmienić to, co widzi operator).
"""

from __future__ import annotations

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
KEEP_CONTROL = {"\t", "\n"}


def strip_control(text: str) -> str:
    """Zostawia tabulator i nową linię, usuwa resztę znaków sterujących (w tym ESC)."""
    return "".join(ch for ch in text if ch in KEEP_CONTROL or ord(ch) >= 32)


def sanitize_text(text: str) -> str:
    """Usuwa znaki sterujące i neutralizuje prefiksy interpretowane jako formuła."""
    cleaned = strip_control(text)
    if cleaned.startswith(FORMULA_PREFIXES):
        return "'" + cleaned
    return cleaned
