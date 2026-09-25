"""Stałe wspólne dla modułów wiringu drzwi Teams Graph."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_MISSING_TEAMS_GRAPH = (
    "Drzwi Teams (delegowane) wymagają extra 'teams-graph'. Zainstaluj: uv sync --extra teams-graph"
)
