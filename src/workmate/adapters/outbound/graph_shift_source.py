"""Odczyt opublikowanych zmian z Microsoft Shifts przez Graph (ADR 0036) — port ``ShiftSource``.

Wzorzec jak ``fetch_team_members``: synchroniczny ``httpx``, stronicowanie ``@odata.nextLink`` z
twardym capem — ucięcie to TWARDY błąd, nie cichy brak: niepełny grafik zaniżyłby godziny w arkuszu
importowanym jako FAKT. Import ``httpx`` leniwy (extra ``worklogi``). Bierzemy WYŁĄCZNIE
opublikowane (``sharedShift``) — wersje robocze (``draftShift``) to nie jest przepracowany czas.

Filtrowanie po oknie tygodnia i podział na doby NIE są tutaj — robi je rdzeń
(``shift_hours.minutes_by_person_day``); adapter tylko pobiera i parsuje (jak ``JsonHoursSource``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from workmate.core.domain.shift_hours import ShiftBlock

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.microsoft.com/v1.0"
# Cap stron — backstop przed pętlą. Zespół to kilkanaście osób, kilka tygodni grafiku; 50 stron to
# zapas ponad potrzebę, a wyczerpanie capu jest anomalią (patrz fail-loud niżej).
_MAX_SHIFT_PAGES = 50
_FRACTION = re.compile(r"\.(\d+)")


def _parse_graph_datetime(value: str) -> datetime:
    """ISO 8601 z Graph (``…Z``, ułamek nawet 7-cyfrowy) → świadomy ``datetime`` (UTC).

    ``fromisoformat`` poniżej 3.11 nie znosi ``Z`` ani >6 cyfr ułamka, więc oba normalizujemy.
    """
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = _FRACTION.sub(lambda m: "." + m.group(1)[:6], text)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def shift_block_from_json(raw: dict[str, Any]) -> ShiftBlock | None:
    """Wpis ``/schedule/shifts`` → ``ShiftBlock`` z OPUBLIKOWANEJ zmiany (albo ``None``).

    ``None``, gdy brak ``userId``/``sharedShift``/dat, zła data lub zły zakres (koniec ≤ start).
    Wersje robocze (``draftShift``) świadomie POMIJAMY — to nie jest przepracowany czas.
    """
    user_id = raw.get("userId")
    body = raw.get("sharedShift")
    if not user_id or not isinstance(body, dict):
        return None
    start_raw = body.get("startDateTime")
    end_raw = body.get("endDateTime")
    if not start_raw or not end_raw:
        return None
    try:
        start = _parse_graph_datetime(str(start_raw))
        end = _parse_graph_datetime(str(end_raw))
    except ValueError:
        return None
    if end <= start:
        return None
    return ShiftBlock(user_id=str(user_id), start=start, end=end)


class GraphShiftSource:
    """``ShiftSource`` czytający opublikowane zmiany zespołu z Graph.

    ``client`` można wstrzyknąć (test na ``httpx.MockTransport``); w produkcji ``None`` → własny,
    krótkotrwały ``httpx.Client`` na czas odczytu (przebieg jest wsadowy, koszt bez znaczenia).
    """

    def __init__(
        self,
        token: Callable[[], str],
        team_id: str,
        *,
        client: Any = None,
        timeout: float = 30.0,
    ) -> None:
        self._token = token
        self._team_id = team_id
        self._client = client
        self._timeout = timeout

    def read_blocks(self) -> list[ShiftBlock]:
        if self._client is not None:
            return self._fetch(self._client)
        import httpx

        with httpx.Client(timeout=self._timeout) as client:
            return self._fetch(client)

    def _fetch(self, client: Any) -> list[ShiftBlock]:
        headers = {"Authorization": f"Bearer {self._token()}", "Accept": "application/json"}
        blocks: list[ShiftBlock] = []
        url: str | None = f"{_GRAPH}/teams/{self._team_id}/schedule/shifts"
        for _ in range(_MAX_SHIFT_PAGES):
            if not url:
                break
            response = client.get(url, headers=headers)
            response.raise_for_status()
            body = response.json()
            for item in body.get("value", []):
                if not isinstance(item, dict):
                    continue
                block = shift_block_from_json(item)
                if block is not None:
                    blocks.append(block)
            url = body.get("@odata.nextLink")
        if url:
            # Cap wyczerpany, a Graph ma jeszcze strony — TWARDY błąd. Niepełny grafik = zaniżone
            # godziny w arkuszu importowanym jako fakt; lepiej paść i ponowić niż wysłać za mało.
            raise ValueError(
                f"lista zmian zespołu {self._team_id} jest NIEKOMPLETNA: przerwano po "
                f"{_MAX_SHIFT_PAGES} stronach, a Graph ma kolejne ({len(blocks)} bloków). "
                "Zaniżyłoby to godziny — podnieś cap stron."
            )
        logger.info("Zespół %s: %d bloków zmian z Graph.", self._team_id, len(blocks))
        return blocks
