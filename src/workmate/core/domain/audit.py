"""Dziennik audytu (Faza 0, ADR 0067) — czysta projekcja argumentów narzędzia do zapisu BEZ treści.

Rejestrujemy AKCJE i ŚCIEŻKI, nigdy treść. Pole spoza allowlisty — i pole allowlisty, które jest
podejrzanie długie albo nieskalarne — redukujemy do znacznika ``<typ:długość>``. Klucz pola zostaje
zawsze: audyt ma wiedzieć, JAKIE argumenty padły, nie jaka była ich treść. Nowe narzędzie jest
domyślnie w pełni zredagowane, dopóki jego pole nie trafi na allowlistę — bezpieczna wartość
domyślna (ADR 0067 §1.3). Body notatki, bajty pliku i komenda ``Bash`` NIGDY nie trafiają do zapisu.

Bez I/O: to czysta funkcja, testowalna bez bazy. Fizyczny zapis stoi w adapterze, pseudonimizacja
nadawcy/rozmowy w warstwie aplikacji (reużywa ``core.domain.metrics.pseudonymize``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Pola STRUKTURALNE bezpieczne do zapisu wprost (akcja / identyfikator / ścieżka), wspólne dla
# narzędzi. Wszystko poza tą listą to potencjalna treść → znacznik typu i długości. Świadomie
# WĄSKA: łatwiej dopisać pole, gdy pomiar pokaże, że go brakuje, niż wykryć wyciek treści po fakcie.
_SAFE_FIELDS = frozenset(
    {
        "action",
        "key",
        "number",
        "issue",
        "project",
        "company",
        "note_id",
        "id",
        "kind",
        "source",
        "format",
        "filename",
        "path",
        "limit",
    }
)

# Nawet pole z allowlisty redagujemy, gdy jest podejrzanie długie — „path" o 2 kB to nie ścieżka,
# tylko treść wciśnięta w strukturalnie wyglądające pole.
_MAX_FIELD_CHARS = 128


def _redact(value: Any) -> str:
    """Znacznik ``<typ:rozmiar>`` bez treści: długość str/bytes, liczba elementów kolekcji."""
    if isinstance(value, str):
        return f"<str:{len(value)}>"
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, (list, tuple, set)):
        return f"<{type(value).__name__}:{len(value)}>"
    if isinstance(value, Mapping):
        return f"<dict:{len(value)}>"
    return f"<{type(value).__name__}>"


def _project_value(name: str, value: Any) -> Any:
    """Zostaw wartość pola strukturalnego (krótką, skalarną); w innym wypadku zwróć znacznik."""
    if name not in _SAFE_FIELDS:
        return _redact(value)
    # ``bool`` jest podklasą ``int`` — obie (i ``float``) to bezpieczne skalary, bez treści.
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and len(value) <= _MAX_FIELD_CHARS:
        return value
    return _redact(value)


def project_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Zredaguj argumenty narzędzia do zapisu: pola strukturalne zostają, reszta → znaczniki.

    Zwraca słownik JSON-serializowalny (skalary albo znaczniki ``<typ:długość>``). Kolejność
    kluczy zachowana dla czytelności dziennika.
    """
    return {name: _project_value(name, value) for name, value in arguments.items()}
