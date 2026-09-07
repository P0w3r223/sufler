"""Adaptacyjny odstęp odpytywania: gęsto tuż po aktywności, wolniej w miarę ciszy (do limitu).

Czysta funkcja (wstrzykiwany ``now``/``last_activity``), jak ``next_run`` — w pełni testowalna.
Operuje na elapsed-seconds w UTC, więc jest niewrażliwa na zmianę czasu (DST).
"""

from __future__ import annotations

from datetime import datetime


def next_poll_delay(
    *, now: datetime, last_activity: datetime, base_s: float, max_s: float, factor: float = 2.0
) -> float:
    """Sekundy do następnego odpytania: ``base_s`` tuż po aktywności, rośnie geometrycznie z ciszą.

    Cisza = ``now - last_activity``. Opóźnienie startuje od ``base_s`` i mnoży się przez ``factor``,
    dopóki nie pokryje ciszy (albo nie osiągnie limitu ``max_s``). Ujemna cisza (serwer przed
    zegarem lokalnym) przycięta do 0 → ``base_s``. ``factor <= 1`` lub ``base_s <= 0`` daje stały
    ``base_s`` (bez nieskończonej pętli — mnożenie 0 nie urośnie). Wynik w [``base_s``, ``max_s``].
    """
    idle_s = max(0.0, (now - last_activity).total_seconds())
    delay = base_s
    if factor > 1.0 and delay > 0.0:
        while delay < idle_s and delay < max_s:
            delay *= factor
    return min(delay, max_s)
