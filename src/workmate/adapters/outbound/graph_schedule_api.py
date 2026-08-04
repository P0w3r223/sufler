"""Klient grafiku Teams Shifts (Microsoft Graph v1.0, ``httpx.Client``) — port odczytu grafiku.

Sync (jak klienci Jiry) — narzędzia wołają go w puli wątków. Token bierzemy z ``token_provider``
przy KAŻDYM żądaniu (cichy token z cudzego cache MSAL, ADR 0056), więc rotacja po stronie tamtego
bota jest przezroczysta. Transport przez wspólne ``graph_http.request_with_retry`` (retry na
429/5xx dla GET-ów — wszystko tu jest GET-em i idempotentne).

Zapytania o zmiany/nieobecności filtrujemy w Graph tylko formą ``ge/le`` (jedyną wspieraną przez
schedule) na POSZERZONYM oknie; dokładny filtr nakładania robi domena (``schedule``).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from workmate.adapters.outbound.graph_http import request_with_retry
from workmate.core.errors import ScheduleReadError

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
# Cap stron paginacji (@odata.nextLink) — ochrona przed nieograniczonym chainingiem.
_MAX_PAGES = 20
# Bufor okna zapytania zmian: schedule/shifts pozwala TYLKO ``startDateTime ge`` i ``endDateTime le``
# (potwierdzone empirycznie — inne operatory/powtórzenia dają 400). Filtrujemy więc na oknie
# poszerzonym o dobę z każdej strony (zmiany są krótkie, dobowe), a dokładny overlap robi domena.
_SHIFT_BUFFER_DAYS = 1


class HttpxGraphScheduleClient:
    """Klient ``/teams/{id}/schedule`` — członkowie, zmiany, nieobecności, powody (odczyt)."""

    def __init__(
        self,
        client: httpx.Client,
        token_provider: Any,
        *,
        base_url: str = _GRAPH_BASE,
    ) -> None:
        self._client = client
        self._token_provider = token_provider
        self._base_url = base_url.rstrip("/")

    def list_members(self, team_id: str) -> list[dict[str, Any]]:
        """Członkowie zespołu (aadUserConversationMember → userId + displayName)."""
        members: list[dict[str, Any]] = []
        url = f"{self._base_url}/teams/{team_id}/members"
        for _ in range(_MAX_PAGES):
            body = self._get(url)
            raw = body.get("value")
            page = [m for m in raw if isinstance(m, dict)] if isinstance(raw, list) else []
            members.extend(page)
            url = body.get("@odata.nextLink") or ""
            if not url:
                break
        return members

    def list_shifts(
        self, team_id: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        from datetime import timedelta

        buffer = timedelta(days=_SHIFT_BUFFER_DAYS)
        filt = _window_filter("sharedShift", start - buffer, end + buffer)
        return self._get_paged(f"{self._base_url}/teams/{team_id}/schedule/shifts", filt)

    def list_times_off(
        self, team_id: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        # BEZ filtra serwerowego: ``endDateTime le`` wykluczyłoby długą nieobecność KOŃCZĄCĄ SIĘ po
        # oknie (urlop trwający dalej), a to najczęstsze pytanie („kto ma teraz wolne"). Nieobecności
        # jest mało (dziesiątki na cały zespół), więc pobieramy wszystkie i overlap robi domena.
        return self._get_paged(f"{self._base_url}/teams/{team_id}/schedule/timesOff", None)

    def list_time_off_reasons(self, team_id: str) -> dict[str, str]:
        reasons: dict[str, str] = {}
        for item in self._get_paged(
            f"{self._base_url}/teams/{team_id}/schedule/timeOffReasons", None
        ):
            rid = str(item.get("id") or "")
            if rid:
                reasons[rid] = str(item.get("displayName") or "")
        return reasons

    # --- transport ---------------------------------------------------------------

    def _get_paged(self, url: str, filt: str | None) -> list[dict[str, Any]]:
        from urllib.parse import quote

        # ``$filter`` ma spacje i ukośniki — kodujemy je, bo składamy URL ręcznie (transport
        # ``request_with_retry`` nie przyjmuje ``params``). Kolejne strony to gotowe, już zakodowane
        # ``@odata.nextLink``, więc je podajemy bez zmian.
        next_url = f"{url}?$filter={quote(filt)}" if filt else url
        items: list[dict[str, Any]] = []
        for _ in range(_MAX_PAGES):
            body = self._get(next_url)
            raw = body.get("value")
            page = [i for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []
            items.extend(page)
            next_url = body.get("@odata.nextLink") or ""
            if not next_url:
                break
        return items

    def _get(self, url: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token_provider()}"}
        try:
            response = request_with_retry(
                self._client, "GET", url, headers=headers, retry_transient=True
            )
        except httpx.HTTPStatusError as exc:
            raise ScheduleReadError(_status_message(exc.response.status_code)) from exc
        except httpx.TimeoutException as exc:
            raise ScheduleReadError(
                "Graf grafiku nie odpowiedział w czasie (timeout) — spróbuj ponownie."
            ) from exc
        except httpx.HTTPError as exc:
            raise ScheduleReadError(f"nie udało się połączyć z grafikiem Teams: {exc}.") from exc
        data = response.json()
        return data if isinstance(data, dict) else {}


def _window_filter(prefix: str, start: datetime, end: datetime) -> str:
    """Zbuduj ``$filter`` Graph: ``startDateTime ge lo AND endDateTime le hi``.

    To JEDYNA forma, którą schedule/shifts akceptuje (``startDateTime`` przyjmuje tylko ``ge``,
    ``endDateTime`` tylko ``le`` — inne operatory albo powtórzona właściwość → 400).
    """
    lo = _graph_iso(start)
    hi = _graph_iso(end)
    return f"{prefix}/startDateTime ge {lo} and {prefix}/endDateTime le {hi}"


def _graph_iso(value: datetime) -> str:
    """Znacznik czasu w formacie Graph (UTC, ``...Z``) — bez mikrosekund."""
    from datetime import timezone

    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _status_message(status_code: int) -> str:
    if status_code in (401, 403):
        return (
            "brak dostępu do grafiku Teams — aplikacja może nie mieć zgody na Schedule.Read.All "
            "albo sesja bota powiadomienia-teams wygasła."
        )
    if status_code == 404:
        return "nie znaleziono grafiku dla tego zespołu (sprawdź, czy zespół ma włączony grafik)."
    if status_code == 429:
        return "Microsoft Graph ogranicza liczbę żądań (429) — spróbuj ponownie za chwilę."
    return f"grafik Teams odpowiedział błędem (HTTP {status_code})."
