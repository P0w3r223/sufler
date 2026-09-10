"""Typy współdzielone między `store`, `normalizer` i `exporter` — bez I/O."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

ZRODLO_API = "CEIDG_API"
ZRODLO_RAPORT = "CEIDG_RAPORT"

Zrodlo = Literal["CEIDG_API", "CEIDG_RAPORT"]
DetailState = Literal["brak", "pobrany", "nieznaleziony", "blad"]


@dataclass(frozen=True)
class RawRecord:
    """Surowy rekord z bazy: element `firmy[]` z listy i/lub element `firma[]` ze szczegółów."""

    id: str
    list_json: Mapping[str, Any] | None
    detail_json: Mapping[str, Any] | None
    list_utc: str | None
    detail_utc: str | None
    detail_state: str
    zrodlo: str


@dataclass(frozen=True)
class Report:
    """Pozycja z `/raporty`: identyfikator, nazwa, format, link do pobrania, data utworzenia."""

    id: str
    nazwa: str
    format: str
    url: str
    utworzono: str


@dataclass(frozen=True)
class RowContext:
    """Pochodzenie wiersza przekazywane jawnie — normalizer nie czyta stanu globalnego."""

    srodowisko: str
    pobrano_utc: str
