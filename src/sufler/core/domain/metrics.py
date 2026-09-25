"""Metryki użycia bota (Tor A) — czyste bucketing tygodnia i pseudonimizacja nadawcy.

Bez I/O: agregację liczy magazyn (SQL), a tu żyje tylko to, co MUSI być spójne i testowalne
bez bazy — klucz tygodnia ISO (do „unikalnych/tydzień" i „powracających") oraz pseudonim
użytkownika. PII: surowego AAD id / nazwy nadawcy NIE zapisujemy — do bazy trafia wyłącznie
nieodwracalny, skrócony hash, więc licznik nie staje się rejestrem „kto o co pytał".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

# Strefa „tygodnia" spójna z worklogami (ADR 0035) — granica tygodnia znaczy to samo w całym
# systemie, niezależnie od strefy kontenera.
REPORT_TZ = ZoneInfo("Europe/Warsaw")

# Skrócenie hashu: 16 znaków hex (64 bity) wystarcza do rozróżnienia kilkudziesięciu osób pionu
# bez przechowywania pełnego, odwracalnego mapowania — to pseudonim, nie klucz kryptograficzny.
_USER_KEY_LEN = 16


def iso_week(occurred_at: datetime, tz: ZoneInfo = REPORT_TZ) -> str:
    """Klucz tygodnia ISO ``RRRR-Www`` z LOKALNEJ daty zdarzenia (np. ``2026-W31``).

    Naiwny ``datetime`` (szew podaje naiwny UTC — spójny z zegarem rozmów) traktujemy jako UTC,
    a nie jako czas systemowy: inaczej ``astimezone`` przy ``TZ`` kontenera ≠ UTC przesunąłby
    wywołanie na granicy tygodnia do sąsiedniego tygodnia (przekłamanie „powracających").
    """
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)
    year, week, _ = occurred_at.astimezone(tz).isocalendar()
    return f"{year:04d}-W{week:02d}"


def pseudonymize(raw_user: str) -> str:
    """Nieodwracalny pseudonim nadawcy: skrócony ``sha256`` hex; pusty id → ``"anon"``.

    Pusty identyfikator to drzwi bez tożsamości nadawcy (np. CLI operatora) — liczymy je jako
    jednego „anonimowego" użytkownika, zamiast gubić wywołanie.
    """
    key = raw_user.strip()
    if not key:
        return "anon"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:_USER_KEY_LEN]


@dataclass(frozen=True)
class DoorUsage:
    """Zwinięta metryka jednych drzwi.

    ``returning_users`` = użytkownicy widziani w ≥2 RÓŻNYCH tygodniach ISO (miara powracalności,
    nie sama liczba wywołań).
    """

    door: str
    calls: int
    unique_users: int
    returning_users: int


@dataclass(frozen=True)
class MetricsSummary:
    """Zestawienie metryk per drzwi (posortowane malejąco po liczbie wywołań)."""

    by_door: tuple[DoorUsage, ...] = field(default_factory=tuple)
