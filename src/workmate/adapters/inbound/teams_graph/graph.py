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
from workmate.adapters.outbound.graph_http import retry_after_s
from workmate.core.errors import ThreadRootGone

GRAPH = "https://graph.microsoft.com/v1.0"
# Twardy cap pobrania publicznego obrazu (GIF/emoji) — zewnętrzny host, którego nie kontrolujemy;
# strumieniujemy i przerywamy powyżej, by nie wpuścić gigabajtów do RAM przed limitem materializera.
_PUBLIC_FETCH_MAX_BYTES = 50 * 1024 * 1024
# Sufit ponowień na 429 w jednym żądaniu — po wyczerpaniu podnosimy błąd, żeby pętla
# pollingu odizolowała zablokowany kanał i przeszła do kolejnych (zamiast utknąć bez końca).
_MAX_429_RETRIES = 5


class HttpxGraphChannelClient:
    """Konkretny klient Graph oparty o ``httpx.AsyncClient`` i dostawcę tokenu."""

    def __init__(self, client: httpx.AsyncClient, token_provider: Callable[[], str]) -> None:
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

    async def _get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        attempts = 0
        refreshed = False
        while True:
            response = await self._client.get(url, params=params)
            if response.status_code == 429 and attempts < _MAX_429_RETRIES:
                attempts += 1
                await asyncio.sleep(retry_after_s(response))
                continue
            # Przejściowy 401 to NIE wygaśnięcie (wtedy 401 dostałaby cała runda) — Graph
            # potrafi je zwrócić przy odświeżaniu tokenu albo lagu replik. Wymuszamy jedno
            # odświeżenie nagłówka i ponawiamy RAZ; realny brak uprawnień poleci niżej jak
            # dziś. Bez tego każdy taki 401 kosztuje jeden zgubiony cykl pollingu.
            if response.status_code == 401 and not refreshed:
                refreshed = True
                await self.refresh_auth()
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
        refreshed = False
        while True:
            response = await self._client.get(url, follow_redirects=follow_redirects)
            if response.status_code == 429 and attempts < _MAX_429_RETRIES:
                attempts += 1
                await asyncio.sleep(retry_after_s(response))
                continue
            if response.status_code == 401 and not refreshed:  # patrz ``_get``: 401-refresh raz
                refreshed = True
                await self.refresh_auth()
                continue
            response.raise_for_status()
            return response.content

    async def get_me_id(self) -> str:
        data = await self._get(f"{GRAPH}/me")
        return str(data["id"])

    async def get_hosted_content(
        self, team_id: str, channel_id: str, root_id: str, message_id: str, hosted_id: str
    ) -> bytes:
        """Bajty obrazu wklejonego inline (hostedContents) — na obecnym zakresie kanału.

        Wklejka w POŚCIE-ROOT jest pod ``…/messages/{root_id}/hostedContents`` (wtedy
        ``message_id == root_id``); wklejka w ODPOWIEDZI wymaga ścieżki REPLY-scoped
        ``…/messages/{root_id}/replies/{reply_id}/hostedContents`` — Graph NIE adresuje repliki
        pod top-level ``/messages/{id}`` — root-scope dla repliki daje 404 (by-id i listowanie).

        Id z ``<img src>`` w treści bywa nie-do-zmapowania przez proxy Graph (404). Wtedy
        próbujemy jeszcze AUTORYTATYWNYCH id z LISTOWANIA ``/hostedContents`` — część wklejek
        (obiekty ``asm.skype``) schodzi dopiero tak. Obiekty ``asyncgw`` i tak nie zejdą.
        """
        base = f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages/{root_id}"
        if message_id != root_id:  # odpowiedź → reply-scoped (root-scope dałby 404)
            base += f"/replies/{message_id}"
        base += "/hostedContents"
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
        try:
            return await self._get_all(url, params={"$top": str(top)})
        except httpx.HTTPStatusError as exc:
            # 404 = root skasowany w Teams. Strona ZAPISU (``graph_thread_reply``) już podnosi
            # tu ``ThreadRootGone``; strona odczytu dawała surowy ``HTTPStatusError``, więc poller
            # nie umiał odróżnić „wątek zniknął" (samoleczenie: eksmituj) od awarii odczytu
            # (przejściowa: nie ruszaj wątku). Nadajemy temu 404 to samo słownictwo.
            if exc.response.status_code == 404:
                raise ThreadRootGone(f"root wątku {root_id} nie istnieje") from exc
            raise

    async def post_reply(self, team_id: str, channel_id: str, root_id: str, text: str) -> None:
        """Wyślij odpowiedź w wątku; Markdown agenta renderujemy do HTML na wyjściu.

        Teams renderuje ``contentType: "html"`` (podzbiór tagów), więc surowy Markdown
        (``**``, ``###``, listy) zamieniamy tu na czytelny HTML — inaczej znaki wychodzą
        dosłownie. Sama konwersja żyje w ``formatting.to_teams_html`` (transport zostaje cienki).
        """
        url = f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages/{root_id}/replies"
        payload = {"body": {"contentType": "html", "content": to_teams_html(text)}}
        attempts = 0
        refreshed = False
        while True:
            response = await self._client.post(url, json=payload)
            if response.status_code == 429 and attempts < _MAX_429_RETRIES:
                attempts += 1
                await asyncio.sleep(retry_after_s(response))
                continue
            if response.status_code == 401 and not refreshed:  # patrz ``_get``: 401-refresh raz
                refreshed = True
                await self.refresh_auth()
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


# Czekanie po 429 liczy WSPÓLNA funkcja transportu (``graph_http.retry_after_s``) — ta sama, co
# u klientów wychodzących Graph i Jiry. Lokalna wersja brała wartość z nagłówka bez sufitu, więc
# serwer sterował długością snu pętli pollingu: przy `Retry-After: 300` pojedyncze
# `list_root_messages` spało pięć minut bez bicia pulsu, a healthcheck floty restartował kontener
# w połowie rundy. Reszta rodziny miała sufit i testy od początku — tu był rozjazd, nie decyzja.
