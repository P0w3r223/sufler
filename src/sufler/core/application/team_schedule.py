"""Grafik zespołu z Teams Shifts (ADR 0059) — jedna odczytowa zdolność, zero mutacji.

Składa surowe dane z portu grafiku (członkowie + zmiany + nieobecności + powody) w gotową odpowiedź
dla agenta: zmiany i nieobecności w zadanym oknie, w strefie pionu, opcjonalnie zawężone do jednej
osoby. Osobę rozwiązujemy po nazwisku na ZAUFANEJ liście członków zespołu z Graph (nie zgadywanie) —
dlatego „Jerzy Zastepski" działa, choć nie ma go w mapie tożsamości Jiry.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sufler.core.domain.names import match_name
from sufler.core.domain.schedule import (
    map_shifts,
    map_times_off,
    resolve_schedule_range,
)
from sufler.core.errors import InvalidRequestError

if TYPE_CHECKING:
    from sufler.core.ports.schedule import ScheduleReadPort

# Sufit wpisów w odpowiedzi — grafik dużego zespołu na miesiąc mógłby być ogromny; podgląd to
# tydzień/dwa, po więcej idzie się węższym zakresem albo filtrem osoby.
_MAX_ENTRIES = 200


class TeamScheduleService:
    """Zwraca grafik (zmiany + nieobecności) jednego zespołu w zadanym oknie, w strefie pionu."""

    def __init__(
        self,
        client: ScheduleReadPort,
        *,
        team_id: str,
        tz: str,
        now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        self._client = client
        self._team_id = team_id
        self._tz = ZoneInfo(tz)
        # Zegar WSTRZYKNIĘTY, jak w ``worklog`` — rdzeń nie sięga po czas rzeczywisty sam.
        # „Bieżący tydzień" jest funkcją chwili, więc bez tego szwu sonda na granicę tygodnia
        # albo zmianę czasu wymagałaby łatania modułu.
        self._now = now

    def schedule(
        self,
        week: str = "current",
        date_from: str = "",
        date_to: str = "",
        person: str = "",
    ) -> dict[str, Any]:
        """Grafik zespołu w oknie (tydzień lub zakres dat), opcjonalnie dla jednej osoby."""
        now = self._now()
        start, end = resolve_schedule_range(
            week=week, date_from=date_from, date_to=date_to, today=now, tz=self._tz
        )
        members = self._client.list_members(self._team_id)
        members_by_id, candidates = _index_members(members)

        target_user_id = ""
        if person.strip():
            value, ambiguous = match_name(candidates, person)
            if value is None:
                if ambiguous:
                    raise InvalidRequestError(
                        f"Więcej niż jedna osoba pasuje do {person!r}: "
                        f"{', '.join(ambiguous)} — doprecyzuj imię i nazwisko."
                    )
                raise InvalidRequestError(
                    f"Nie znajduję osoby {person!r} w zespole. Dostępni: "
                    f"{', '.join(sorted(members_by_id.values())) or '(brak)'}."
                )
            target_user_id = value

        raw_shifts = self._client.list_shifts(self._team_id, start, end)
        raw_times_off = self._client.list_times_off(self._team_id, start, end)
        reasons = self._client.list_time_off_reasons(self._team_id)

        if target_user_id:
            raw_shifts = [s for s in raw_shifts if str(s.get("userId") or "") == target_user_id]
            raw_times_off = [
                t for t in raw_times_off if str(t.get("userId") or "") == target_user_id
            ]

        shifts = map_shifts(raw_shifts, members_by_id, window=(start, end), tz=self._tz)
        times_off = map_times_off(
            raw_times_off, members_by_id, reasons, window=(start, end), tz=self._tz
        )
        with_entries = {e.person for e in shifts} | {e.person for e in times_off}
        without = sorted(v for v in members_by_id.values() if v not in with_entries)
        # Sufit MUSI być widoczny w odpowiedzi. ``people_without_entries`` liczy się z PEŁNYCH list,
        # więc przy cichym przycięciu dwa pola tej samej odpowiedzi przeczyły sobie: osoba miała
        # zmianę (nie było jej wśród „bez wpisów"), a w `shifts` po niej nie było śladu. Wzorzec
        # ``truncated`` jest w tym systemie ustalony (historia Jiry, wyszukiwanie zdarzeń).
        pominietych = max(0, len(shifts) - _MAX_ENTRIES) + max(0, len(times_off) - _MAX_ENTRIES)
        return {
            # ``end`` jest wykładniczy (półotwarty) — pokazujemy WŁĄCZNY ostatni dzień, czytelniej.
            "range": {
                "from": start.date().isoformat(),
                "to": (end - timedelta(days=1)).date().isoformat(),
            },
            "timezone": str(self._tz),
            "shifts": [e.model_dump(mode="json") for e in shifts[:_MAX_ENTRIES]],
            "times_off": [e.model_dump(mode="json") for e in times_off[:_MAX_ENTRIES]],
            "truncated": pominietych > 0,
            "omitted_entries": pominietych,
            "people_without_entries": [] if person.strip() else without,
        }


def _index_members(
    members: list[dict[str, Any]],
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Zbuduj mapę ``userId → displayName`` i listę ``(displayName, userId)`` do dopasowania
    osoby."""
    by_id: dict[str, str] = {}
    candidates: list[tuple[str, str]] = []
    for member in members:
        if not isinstance(member, dict):
            continue
        user_id = str(member.get("userId") or "")
        display = str(member.get("displayName") or "")
        if not user_id or not display:
            continue
        by_id[user_id] = display
        candidates.append((display, user_id))
    return by_id, candidates
