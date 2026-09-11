"""Neutralizacja wrogich napisów z rejestru — moduł czysty.

Skopiowany dosłownie z `ceidg-tool/ceidg_tool/safetext.py` (kopia z 2026-09-10). To jedyny
plik przeniesiony bez zmian merytorycznych.

Dane z rejestru traktujemy jako wrogie: nazwę spółki wpisuje do KRS człowiek, a odpis czyta
potem terminal (gdzie sekwencja ESC steruje ekranem) i markdown (gdzie `](http://…)` robi
odnośnik). `strip_control` obsługuje część wspólną obu kanałów.

`sanitize_text` (prefiks formuły) wchodzi mimo braku arkusza w etapie 1 — kosztuje zero,
a raport kiedyś nim będzie. **Dziś nic go nie ćwiczy**, i to zdanie jest tu po to, żeby nikt
nie wziął jego obecności za dowód, że ścieżka arkuszowa jest sprawdzona.
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
