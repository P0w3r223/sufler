"""Koszt pobrania liczony z `count` i profilu API — moduł czysty, bez sieci i bez bazy.

Wydzielony z `pipeline`, żeby warstwa tekstów (`ui/texts.py`) mogła liczyć koszty,
nie importując klienta HTTP ani SQLite (ADR-0008, reguła granic 6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .apiprofile import ApiProfile

# uzupelnienie-01.md §C: powyżej progu program proponuje zawężenie albo podział na partie.
LARGE_COUNT_THRESHOLD = 50_000


@dataclass(frozen=True)
class Estimate:
    """Koszt pobrania wyliczony z `count` i profilu — czysta funkcja, bez sieci."""

    count: int
    page_limit: int
    ids_batch: int
    spacing_s: float
    requests_list: int
    requests_details: int

    @property
    def seconds_list(self) -> float:
        return self.requests_list * self.spacing_s

    @property
    def seconds_details(self) -> float:
        return self.requests_details * self.spacing_s

    @property
    def seconds_total(self) -> float:
        return self.seconds_list + self.seconds_details


def effective_spacing(profile: ApiProfile) -> float:
    """Odstęp, jaki narzuca odstęp minimalny albo najciaśniejsze okno limitera."""
    windows = [span / limit for limit, span in profile.rate.windows]
    return max([profile.rate.min_spacing_s, *windows])


def estimate(count: int, profile: ApiProfile, *, max_rekordow: int | None = None) -> Estimate:
    effective = min(count, max_rekordow) if max_rekordow else count
    requests_list = math.ceil(effective / profile.max_limit_firmy) if effective else 0
    requests_details = math.ceil(effective / profile.ids_batch_size) if effective else 0
    return Estimate(
        count=count,
        page_limit=profile.max_limit_firmy,
        ids_batch=profile.ids_batch_size,
        spacing_s=effective_spacing(profile),
        requests_list=requests_list,
        requests_details=requests_details,
    )
