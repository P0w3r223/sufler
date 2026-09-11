"""Zegar jako zależność wstrzykiwana.

Skopiowany z `ceidg-tool/ceidg_tool/clock.py` (kopia z 2026-09-10), okrojony.

**`sleep` wypadł z protokołu i to jest decyzja, nie przeoczenie.** W `ceidg-tool` zegar był
przede wszystkim szwem dla limitera; tutaj nic nie czeka, bo najdłuższą operacją etapu 1 jest
odczyt pliku. `sleep` w protokole zaprasza limiter bez przedmiotu.

Szew wstrzykiwania zostaje, bo znacznik czasu w dzienniku musi być podmienialny — inaczej
`odtworz` (krok 6) nie da się przetestować.

Uwaga na regułę granic 4: `signals/` nie ma prawa tego modułu importować. Każda data w sygnale
pochodzi z odpisu, nie z zegara — patrz `docs/adr/0001`, decyzja 6.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Zegar ścienny do trwałych znaczników."""

    def wall(self) -> float: ...


class SystemClock:
    """Zegar systemowy."""

    def wall(self) -> float:
        return time.time()


def utc_iso(epoch: float) -> str:
    """Znacznik czasu UTC w formacie ISO bez mikrosekund, z sufiksem `Z`."""
    stamp = datetime.fromtimestamp(epoch, tz=UTC).replace(microsecond=0)
    return stamp.isoformat().replace("+00:00", "Z")
