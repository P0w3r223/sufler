"""Zegar jako zależność wstrzykiwana — jedyne miejsce z `time.sleep` w pakiecie."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Zegar monotoniczny do odstępów, ścienny do trwałych znaczników i komunikatów."""

    def monotonic(self) -> float: ...

    def wall(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """Zegar systemowy."""

    def monotonic(self) -> float:
        return time.monotonic()

    def wall(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


def utc_iso(epoch: float) -> str:
    """Znacznik czasu UTC w formacie ISO bez mikrosekund, z sufiksem `Z`."""
    stamp = datetime.fromtimestamp(epoch, tz=UTC).replace(microsecond=0)
    return stamp.isoformat().replace("+00:00", "Z")


def local_hhmm(epoch: float) -> str:
    """Godzina lokalna `HH:MM` do komunikatów typu „wznawiam o …”."""
    return datetime.fromtimestamp(epoch).strftime("%H:%M")
