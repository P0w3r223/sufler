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

from workmate.adapters.inbound.teams_graph.formatting import to_teams_html

GRAPH = "https://graph.microsoft.com/v1.0"
_DEFAULT_RETRY_AFTER_S = 5
# Twardy cap pobrania publicznego obrazu (GIF/emoji) — zewnętrzny host, którego nie kontrolujemy;
# strumieniujemy i przerywamy powyżej, by nie wpuścić gigabajtów do RAM przed limitem materializera.
_PUBLIC_FETCH_MAX_BYTES = 50 * 1024 * 1024
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
        """Bajty obrazu wklejonego inline (hostedContents) — na obecnym zakresie kanału.

        Id z ``<img src>`` w treści bywa nie-do-zmapowania przez proxy Graph (404). Wtedy
        próbujemy jeszcze AUTORYTATYWNYCH id z LISTOWANIA ``/hostedContents`` — część wklejek
        (obiekty ``asm.skype``) schodzi dopiero tak. Obiekty ``asyncgw`` i tak nie zejdą.
        """
        base = (
            f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages/{message_id}"
            f"/hostedContents"
        )
        try:
            return await self._get_bytes(f"{base}/{hosted_id}/$value")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            return await self._get_hosted_from_listing(base, skip=hosted_id)

    async def _get_hosted_from_listing(self, base: str, *, skip: str) -> bytes:
        """Pobierz pierwszy pobieralny hostedContent z listowania (pomijając zepsute/próbowane).

        Best-effort: przy wielu obrazach inline zwraca PIERWSZY pobieralny — dla wiadomości z
        >1 obrazem może zmapować cudzą treść do slotu (rzadkie; ścieżka odpala się tylko na 404).
        """
        listing = await self._get(base)
        for item in listing.get("value", []):
            hid = item.get("id")
            if not hid or hid == skip:
                continue
            try:
                return await self._get_bytes(f"{base}/{hid}/$value")
            except httpx.HTTPStatusError:
                continue
        raise RuntimeError("hostedContents: brak pobieralnego id (obiekt asyncgw/AMS lub puste id)")

    async def download_public_url(self, url: str) -> bytes:
        """Bajty publicznego obrazu (GIF/Giphy, emoji Teams) — zwykły HTTP, BEZ tokenu Graph.

        Osobny klient bez nagłówka ``Authorization``: nie wysyłamy bearera Graph do zewnętrznego
        hosta. Bariera SSRF (allowlista hostów) siedzi w ``selection``, ale waliduje tylko URL
        POCZĄTKOWY — dlatego ``follow_redirects=False``: przekierowanie na host spoza allowlisty
        (np. metadata/adres wewnętrzny) obeszłoby barierę, więc go NIE podążamy. Teams osadza
        bezpośrednie URL-e giphy/cdn, więc redirect nie jest potrzebny. Strumieniujemy z twardym
        capem, by wielki obraz nie wpuścił do RAM gigabajtów przed limitem materializera.
        """
        async with (
            httpx.AsyncClient(timeout=30, follow_redirects=False) as public,
            public.stream("GET", url) as response,
        ):
            if response.is_redirect:
                raise RuntimeError("publiczny obraz przekierowuje — nie podążamy (SSRF)")
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > _PUBLIC_FETCH_MAX_BYTES:
                    raise RuntimeError("publiczny obraz przekracza twardy limit pobrania")
                chunks.append(chunk)
            return b"".join(chunks)

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
        """Wyślij odpowiedź w wątku; Markdown agenta renderujemy do HTML na wyjściu.

        Teams renderuje ``contentType: "html"`` (podzbiór tagów), więc surowy Markdown
        (``**``, ``###``, listy) zamieniamy tu na czytelny HTML — inaczej znaki wychodzą
        dosłownie. Sama konwersja żyje w ``formatting.to_teams_html`` (transport zostaje cienki).
        """
        url = f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages/{root_id}/replies"
        payload = {"body": {"contentType": "html", "content": to_teams_html(text)}}
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
