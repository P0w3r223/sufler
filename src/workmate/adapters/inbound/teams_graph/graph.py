"""Klient Microsoft Graph dla drzwi Teams (delegowany) — ``httpx`` + dostawca tokenu.

Importowany LENIWIE (w ``app.py``), bo wymaga extra ``teams-graph`` (``httpx``).
Implementuje port ``poller.GraphChannelClient`` strukturalnie (duck typing): obsługuje
429/Retry-After i stronicowanie ``@odata.nextLink``. Token odświeżamy przez wstrzyknięty
(synchroniczny — MSAL) ``token_provider``, wołany w puli wątków, by nie blokować pętli.
"""
from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from typing import Any

import httpx

GRAPH = "https://graph.microsoft.com/v1.0"
_DEFAULT_RETRY_AFTER_S = 5
# Sufit ponowień na 429 w jednym żądaniu — po wyczerpaniu podnosimy błąd, żeby pętla
# pollingu odizolowała zablokowany kanał i przeszła do kolejnych (zamiast utknąć bez końca).
_MAX_429_RETRIES = 5


class HttpxGraphChannelClient:
    """Konkretny klient Graph oparty o ``httpx.AsyncClient`` i dostawcę tokenu."""

    def __init__(
        self, client: httpx.AsyncClient, token_provider: Callable[[], str]
    ) -> None:
        self._client = client
        self._token = token_provider

    async def refresh_auth(self) -> None:
        """Ustaw nagłówek Authorization świeżym tokenem (sync MSAL w puli wątków).

        Wołane raz na rundę pollingu; MSAL zwykle odświeża po cichu z cache (natychmiast),
        interaktywne logowanie dzieje się tylko przy pierwszym użyciu.
        """
        loop = asyncio.get_running_loop()
        token = await loop.run_in_executor(None, self._token)
        self._client.headers["Authorization"] = f"Bearer {token}"

    async def _get(
        self, url: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        attempts = 0
        while True:
            response = await self._client.get(url, params=params)
            if response.status_code == 429 and attempts < _MAX_429_RETRIES:
                attempts += 1
                await asyncio.sleep(_retry_after(response))
                continue
            response.raise_for_status()  # 429 po wyczerpaniu prób też tu podniesie
            return response.json()

    async def _get_all(
        self, url: str, params: dict[str, str] | None = None, *, max_pages: int = 1
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        pages = 0
        next_url: str | None = url
        while next_url and pages < max_pages:
            data = await self._get(next_url, params=params)
            items.extend(data.get("value", []))
            next_url = data.get("@odata.nextLink")
            params = None  # nextLink niesie już parametry
            pages += 1
        return items

    async def _get_bytes(self, url: str, *, follow_redirects: bool = False) -> bytes:
        """Pobierz surowe bajty (załącznik) z obsługą 429; ``follow_redirects`` dla /shares."""
        attempts = 0
        while True:
            response = await self._client.get(url, follow_redirects=follow_redirects)
            if response.status_code == 429 and attempts < _MAX_429_RETRIES:
                attempts += 1
                await asyncio.sleep(_retry_after(response))
                continue
            response.raise_for_status()
            return response.content

    async def get_me_id(self) -> str:
        data = await self._get(f"{GRAPH}/me")
        return str(data["id"])

    async def get_hosted_content(
        self, team_id: str, channel_id: str, message_id: str, hosted_id: str
    ) -> bytes:
        """Bajty obrazu wklejonego inline (hostedContents) — na obecnym zakresie kanału."""
        url = (
            f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages/{message_id}"
            f"/hostedContents/{hosted_id}/$value"
        )
        return await self._get_bytes(url)

    async def download_shared_url(self, url: str) -> bytes:
        """Bajty pliku z SharePoint po ``contentUrl`` (driveItem via /shares).

        Wymaga zakresów ``Files.Read.All``/``Sites.Read.All``. ``/driveItem/content``
        zwraca 302 do wstępnie uwierzytelnionego URL-a SharePointu — podążamy za nim;
        httpx zdejmuje nagłówek ``Authorization`` przy przekierowaniu na inny host.
        """
        endpoint = f"{GRAPH}/shares/{_encode_share_id(url)}/driveItem/content"
        return await self._get_bytes(endpoint, follow_redirects=True)

    async def list_root_messages(
        self, team_id: str, channel_id: str, *, top: int
    ) -> list[dict[str, Any]]:
        # Endpoint nie wspiera $orderby — Graph zwraca wiadomości najnowsze-pierwsze;
        # bierzemy jedną stronę $top, a watermark w ``selection`` zakłada, że najświeższe
        # wpisy są właśnie na stronie 1.
        url = f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages"
        return await self._get_all(url, params={"$top": str(top)})

    async def list_replies(
        self, team_id: str, channel_id: str, root_id: str, *, top: int
    ) -> list[dict[str, Any]]:
        url = f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages/{root_id}/replies"
        return await self._get_all(url, params={"$top": str(top)})

    async def post_reply(
        self, team_id: str, channel_id: str, root_id: str, text: str
    ) -> None:
        url = f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages/{root_id}/replies"
        payload = {"body": {"contentType": "text", "content": text}}
        attempts = 0
        while True:
            response = await self._client.post(url, json=payload)
            if response.status_code == 429 and attempts < _MAX_429_RETRIES:
                attempts += 1
                await asyncio.sleep(_retry_after(response))
                continue
            response.raise_for_status()
            return

    async def list_joined_teams(self) -> list[dict[str, Any]]:
        """Zespoły użytkownika (tryb odkrywania — gdy nie ustawiono WATCH)."""
        return await self._get_all(f"{GRAPH}/me/joinedTeams", max_pages=20)

    async def list_channels(self, team_id: str) -> list[dict[str, Any]]:
        """Kanały zespołu (tryb odkrywania)."""
        return await self._get_all(f"{GRAPH}/teams/{team_id}/channels", max_pages=20)


def _encode_share_id(url: str) -> str:
    """Zakoduj URL udostępnienia na Graph share id: ``u!`` + base64url bez dopełnienia."""
    encoded = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
    return f"u!{encoded}"


def _retry_after(response: httpx.Response) -> int:
    """Sekundy odczekania z nagłówka Retry-After (fallback, gdy brak/niepoprawny)."""
    try:
        return int(response.headers.get("Retry-After", _DEFAULT_RETRY_AFTER_S))
    except ValueError:
        return _DEFAULT_RETRY_AFTER_S
