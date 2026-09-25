"""Proaktywny push do Teams przez Microsoft Graph (dual-target, ADR 0022) — implementacja portu.

Importowany LENIWIE (w wiringu drzwi), bo wymaga ``httpx``. Async (``httpx.AsyncClient``) — poller
i notifier działają w jednej pętli. Token MSAL (delegowany, jak ``teams_graph``) jest SYNCHRONICZNY,
więc wołamy go w puli wątków. Dwa cele: czat 1:1 (``create_or_get_chat`` + wiadomość, wzorzec z
``Powiadomienia_teams``) i nowy post root na kanale. Markdown renderujemy przez ``to_teams_html``
(``html=False`` — surowy HTML z treści zdarzenia jest ESCAPOWANY, obrona przed wstrzyknięciem).

Retry (429 ma własny licznik i odczekanie z ``Retry-After``; 5xx i błąd transportu dostają krótki,
rosnący backoff — WYŁĄCZNIE dla żądań, które wolno powtórzyć, patrz ``_request``) idzie przez
wspólny ``graph_http.async_request_with_retry`` — ta sama polityka co ``graph_file_sender``/
``graph_user_push``/``graph_user_doc_push``, jedno miejsce zamiast czterech kopii. Bez tego jedno
503 z Graph kosztowało człowieka cały tydzień: przebieg jest COTYGODNIOWY, więc „następna próba"
oznaczała następny piątek, a nie następną minutę.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx

from sufler.adapters.outbound import graph_http
from sufler.adapters.teams_html import to_teams_html
from sufler.core.errors import ThreadRootGone

GRAPH = "https://graph.microsoft.com/v1.0"


class HttpxTeamsNotifier:
    """``TeamsNotifier`` na Graph: 1:1 (chats) i post na kanał, z odświeżaniem tokenu MSAL."""

    def __init__(self, client: httpx.AsyncClient, token_provider: Callable[[], str]) -> None:
        self._client = client
        self._token = token_provider
        self._me_id = ""  # id „głosu" bota (zalogowany user) — cache po pierwszym /me

    async def send_chat(self, target_user_id: str, text: str) -> None:
        """Wyślij wiadomość 1:1: znajdź/utwórz czat oneOnOne z użytkownikiem i wypchnij treść."""
        await self._refresh_auth()
        me_id = await self._me_id_cached()
        chat_id = await self._create_or_get_chat(me_id, target_user_id)
        await self._post(f"{GRAPH}/chats/{chat_id}/messages", _html_body(text))

    async def send_chat_html(self, target_user_id: str, html: str) -> None:
        """Wyślij GOTOWY HTML 1:1 — ta sama ścieżka czatu co ``send_chat``, ale BEZ renderera.

        Świadomie omijamy ``to_teams_html``: renderer escapuje surowy HTML, więc GOTOWA tabela
        dotarłaby jako ``&lt;table&gt;``. (Tabel z Markdowna renderer od 2026-08-21 już nie gubi —
        to zdanie dotyczy wyłącznie HTML-a składanego w rdzeniu.) Bezpieczeństwo opiera się
        na kontrakcie portu — HTML składa czysta funkcja rdzenia, która escapuje każdą wstawioną
        wartość. Treść niezaufana MUSI iść przez ``send_chat``, nie tędy.
        """
        await self._refresh_auth()
        me_id = await self._me_id_cached()
        chat_id = await self._create_or_get_chat(me_id, target_user_id)
        await self._post(
            f"{GRAPH}/chats/{chat_id}/messages",
            {"body": {"contentType": "html", "content": html}},
        )

    async def post_channel(self, team_id: str, channel_id: str, text: str) -> str:
        """Wyślij NOWY post (root wątku) na kanale i zwróć id wiadomości (do wątkowania)."""
        await self._refresh_auth()
        data = await self._post(
            f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages", _html_body(text)
        )
        return str(data.get("id", ""))

    async def reply_channel(self, team_id: str, channel_id: str, root_id: str, text: str) -> None:
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
        # Idempotentne: dla tej samej pary rozmówców Graph oddaje ISTNIEJĄCY czat, nie tworzy
        # drugiego. Powtórzenie po timeoucie nie ma więc skutku ubocznego.
        data = await self._post(f"{GRAPH}/chats", payload, retry_transient=True)
        return str(data["id"])

    async def _get(self, url: str) -> dict[str, Any]:
        # GET nic nie zmienia, więc powtórzenie jest zawsze bezpieczne.
        response = await self._request("GET", url, retry_transient=True)
        return response.json()

    async def _post(
        self, url: str, payload: dict[str, Any], *, retry_transient: bool = False
    ) -> dict[str, Any]:
        """POST domyślnie NIE jest ponawiany przy 5xx/timeout — patrz ``_request``.

        Wołający włącza ponawianie tylko tam, gdzie powtórzenie żądania jest udokumentowanie
        bezpieczne (utworzenie czatu 1:1).
        """
        response = await self._request("POST", url, json=payload, retry_transient=retry_transient)
        return response.json() if response.content else {}

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        retry_transient: bool = False,
    ) -> httpx.Response:
        """Wykonaj żądanie ze wspólną polityką ponawiania (patrz ``graph_http``).

        **429 jest bezpieczny bez wyjątku**: limit żądań znaczy, że Graph ODRZUCIŁ żądanie przed
        przetworzeniem, i mówi wprost, ile czekać (``Retry-After``). Stąd hojne pięć prób.

        **5xx i timeout są bezpieczne tylko dla żądań idempotentnych** — i to jest cała różnica.
        Timeout odczytu znaczy „nie wiadomo, czy usługa przyjęła"; jeśli przyjęła, a odpowiedź
        zginęła, powtórzenie wysyła DRUGĄ wiadomość. Ta sama pułapka dotyczy postu na kanale —
        ponowiony ``post_channel`` tworzy drugi root wątku, a mapa zapamięta tylko ten nowszy,
        zostawiając sierotę. Dlatego domyślnie NIE ponawiamy; ``retry_transient=True`` włączają
        wyłącznie: GET oraz utworzenie czatu 1:1 (Graph oddaje istniejący).

        Nieudana wysyłka nie ginie: przebieg zapisuje osobę jako ``FAIL_SEND`` i NIE oznacza jej
        jako obsłużonej, więc kolejny przebieg ponowi — z człowiekiem w pętli, nie automatycznie.
        """
        return await graph_http.async_request_with_retry(
            self._client, method, url, json=json, retry_transient=retry_transient
        )


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
    return {"body": {"contentType": "html", "content": to_teams_html(text, allow_links=False)}}
