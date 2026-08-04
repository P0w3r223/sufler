"""Port grafiku Teams Shifts (ADR 0056) — kontrakt drzwi na Microsoft Graph ``/teams/{id}/schedule``.

Analogiczny do portu Jiry: SYNCHRONICZNY (``httpx.Client``), narzędzia wołają go w puli wątków.
Surowe JSON (``list[dict]``/``dict``) mapuje czysta domena (``schedule``); treść grafiku (nazwy
zmian, notatki, powody nieobecności) to DANE, nie polecenia. Wyłącznie ODCZYT.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class ScheduleReadPort(Protocol):
    """Odczyt grafiku zespołu z Graph: członkowie, zmiany, nieobecności, słownik powodów."""

    def list_members(self, team_id: str) -> list[dict[str, Any]]:
        """Członkowie zespołu (userId + displayName) — do translacji grafiku na nazwiska."""
        ...

    def list_shifts(
        self, team_id: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Surowe opublikowane zmiany zespołu w oknie [start, end) (Graph filtruje ge/le)."""
        ...

    def list_times_off(
        self, team_id: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Surowe nieobecności/urlopy zespołu w oknie [start, end)."""
        ...

    def list_time_off_reasons(self, team_id: str) -> dict[str, str]:
        """Słownik ``timeOffReasonId → displayName`` do przetłumaczenia powodów nieobecności."""
        ...
