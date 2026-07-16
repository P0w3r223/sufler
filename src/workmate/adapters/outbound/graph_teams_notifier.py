"""Proaktywny push do Teams przez Microsoft Graph (dual-target, ADR 0022) — implementacja portu.

Importowany LENIWIE (w wiringu drzwi), bo wymaga ``httpx``. Async (``httpx.AsyncClient``) — poller
i notifier działają w jednej pętli. Token MSAL (delegowany, jak ``teams_graph``) jest SYNCHRONICZNY,
więc wołamy go w puli wątków. Dwa cele: czat 1:1 (``create_or_get_chat`` + wiadomość, wzorzec z
``Powiadomienia_teams``) i nowy post root na kanale. Markdown renderujemy przez ``to_teams_html``
(``html=False`` — surowy HTML z treści zdarzenia jest ESCAPOWANY, obrona przed wstrzyknięciem).
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx

from workmate.adapters.inbound.teams_graph.formatting import to_teams_html
from workmate.core.errors import ThreadRootGone

GRAPH = "https://graph.microsoft.com/v1.0"
_MAX_429_RETRIES = 5
_DEFAULT_RETRY_AFTER_S = 5


class HttpxTeamsNotifier:
    """``TeamsNotifier`` na Graph: 1:1 (chats) i post na kanał, z odświeżaniem tokenu MSAL."""

    def __init__(
        self, client: httpx.AsyncClient, token_provider: Callable[[], str]
    ) -> None:
        self._client = client
        self._token = token_provider
        self._me_id = ""  # id „głosu" bota (zalogowany user) — cache po pierwszym /me

    async def send_chat(self, target_user_id: str, text: str) -> None:
        """Wyślij wiadomość 1:1: znajdź/utwórz czat oneOnOne z użytkownikiem i wypchnij treść."""
        await self._refresh_auth()
        me_id = await self._me_id_cached()
        chat_id = await self._create_or_get_chat(me_id, target_user_id)
        await self._post(f"{GRAPH}/chats/{chat_id}/messages", _html_body(text))

    async def post_channel(self, team_id: str, channel_id: str, text: str) -> str:
        """Wyślij NOWY post (root wątku) na kanale i zwróć id wiadomości (do wątkowania)."""
        await self._refresh_auth()
        data = await self._post(
            f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages", _html_body(text)
        )
        return str(data.get("id", ""))

    async def reply_channel(
        self, team_id: str, channel_id: str, root_id: str, text: str
    ) -> None:
        """Wyślij odpowiedź w istniejącym wątku (``root_id``) — dokładamy do roota, nie tworzymy.

        Gdy root został usunięty (Graph 404), podnosimy ``ThreadRootGone`` — notifier utworzy nowy
        root zamiast blokować cały strumień na usuniętym wątku.
        """
        await self._refresh_auth()
        url = f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages/{root_id}/replies"
        try:
            await self._post(url, _html_body(text))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ThreadRootGone(f"root wątku {root_id} nie istnieje") from exc
            raise

    async def _refresh_auth(self) -> None:
        """Ustaw nagłówek Authorization świeżym tokenem (sync MSAL w puli wątków, cichy refresh)."""
        loop = asyncio.get_running_loop()
        token = await loop.run_in_executor(None, self._token)
        self._client.headers["Authorization"] = f"Bearer {token}"

    async def _me_id_cached(self) -> str:
        if not self._me_id:
            data = await self._get(f"{GRAPH}/me")
            self._me_id = str(data["id"])
        return self._me_id

    async def _create_or_get_chat(self, me_id: str, target_user_id: str) -> str:
        """Znajdź/utwórz czat 1:1 (POST /chats oneOnOne jest idempotentny) i zwróć jego id."""
        payload = {
            "chatType": "oneOnOne",
            "members": [_member(me_id), _member(target_user_id)],
        }
        data = await self._post(f"{GRAPH}/chats", payload)
        return str(data["id"])

    async def _get(self, url: str) -> dict[str, Any]:
        response = await self._request("GET", url)
        return response.json()

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._request("POST", url, json=payload)
        return response.json() if response.content else {}

    async def _request(
        self, method: str, url: str, *, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        attempts = 0
        while True:
            response = await self._client.request(method, url, json=json)
            if response.status_code == 429 and attempts < _MAX_429_RETRIES:
                attempts += 1
                await asyncio.sleep(_retry_after(response))
                continue
            response.raise_for_status()
            return response


def _member(user_id: str) -> dict[str, Any]:
    """Członek czatu 1:1 adresowany przez AAD user id (wzorzec Powiadomienia_teams)."""
    return {
        "@odata.type": "#microsoft.graph.aadUserConversationMember",
        "roles": ["owner"],
        "user@odata.bind": f"{GRAPH}/users('{user_id}')",
    }


def _html_body(text: str) -> dict[str, Any]:
    """Ciało wiadomości Graph: Markdown → HTML, ``contentType: html``.

    ``allow_links=False``: treść zdarzeń pochodzi ze źródła niezaufanego (GitHub), więc markdownowe
    linki nie mogą stać się klikalne (anty-phishing) — prawdziwy URL i tak jest w treści osobno.
    """
    return {
        "body": {"contentType": "html", "content": to_teams_html(text, allow_links=False)}
    }


def _retry_after(response: httpx.Response) -> int:
    """Sekundy odczekania z nagłówka Retry-After (fallback, gdy brak/niepoprawny)."""
    try:
        return int(response.headers.get("Retry-After", _DEFAULT_RETRY_AFTER_S))
    except ValueError:
        return _DEFAULT_RETRY_AFTER_S
