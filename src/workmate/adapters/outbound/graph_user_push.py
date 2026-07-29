"""Wyjściowy obraz 1:1 przez Microsoft Graph (ADR 0027) — implementacja ``UserImageSender``.

Importowany LENIWIE (w wiringu drzwi), bo wymaga ``httpx``. SYNCHRONICZNY (``httpx.Client``, nie
``AsyncClient``): narzędzia agenta biegną synchronicznie w puli wątków, więc ścieżka obrazu wygląda
jak zapis GitHub Gate-4 / plik ADR 0026 (``HttpxGraphFileSender``), nie jak async notifier. Token
MSAL (delegowany, ten sam co ``teams_graph``) jest synchroniczny — wołamy go wprost.

Wysłanie obrazu to: (1) ``GET /me`` po id „głosu" bota (cache), (2) ``POST /chats`` (idempotentne
utworzenie/znalezienie czatu 1:1 z odbiorcą, wzorzec ``Powiadomienia_teams``), (3) ``POST
…/messages`` z obrazem INLINE w ``hostedContents`` (bez dysku SharePoint → bez zakresu ``Files.*``).

Polityka ponawiania jak w ``graph_teams_notifier``/``graph_file_sender``: 429 ZAWSZE (żądanie
odrzucone przed przetworzeniem), 5xx/timeout tylko dla bezpiecznych — ``GET`` oraz idempotentne
utworzenie czatu TAK, ale WYSŁANIE WIADOMOŚCI z obrazem NIE (powtórka po niejednoznacznym timeoucie
dołożyłaby drugi obraz).
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from typing import Any

import httpx

GRAPH = "https://graph.microsoft.com/v1.0"
_MAX_429_RETRIES = 5
_DEFAULT_RETRY_AFTER_S = 5
_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})
_MAX_TRANSIENT_RETRIES = 3
_TRANSIENT_BACKOFF_S = 2
# Stały identyfikator hostedContents w obrębie JEDNEJ wiadomości — treść odwołuje się do niego
# przez ``src="../hostedContents/1/$value"``. Jeden obraz na wiadomość, więc "1" wystarcza.
_HOSTED_ID = "1"


class HttpxGraphUserImagePush:
    """``UserImageSender`` na Graph (SYNC): obraz inline w czacie 1:1 z odbiorcą."""

    def __init__(
        self,
        client: httpx.Client,
        token_provider: Callable[[], str],
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._token = token_provider
        self._sleep = sleep  # wstrzykiwalny, by testy nie odczekiwały realnego backoffu
        self._me_id = ""  # id „głosu" bota (zalogowany user) — cache po pierwszym /me

    def send_image_to_user(self, target_user_id: str, content: bytes, content_type: str) -> None:
        """Wyślij obraz inline w czacie 1:1 z ``target_user_id`` (czat idempotentny).

        POST wiadomości NIE jest ponawiany — powtórka po niejednoznacznym timeoucie = drugi obraz.
        """
        self._refresh_auth()
        me_id = self._me_id_cached()
        chat_id = self._create_or_get_chat(me_id, target_user_id)
        self._post(f"{GRAPH}/chats/{chat_id}/messages", _image_message(content, content_type))

    def _refresh_auth(self) -> None:
        """Ustaw nagłówek Authorization świeżym tokenem (sync MSAL, cichy refresh z cache)."""
        self._client.headers["Authorization"] = f"Bearer {self._token()}"

    def _me_id_cached(self) -> str:
        if not self._me_id:
            data = self._get(f"{GRAPH}/me")
            self._me_id = str(data["id"])
        return self._me_id

    def _create_or_get_chat(self, me_id: str, target_user_id: str) -> str:
        """Znajdź/utwórz czat 1:1 (POST /chats oneOnOne jest idempotentny) i zwróć jego id."""
        payload = {
            "chatType": "oneOnOne",
            "members": [_member(me_id), _member(target_user_id)],
        }
        # Idempotentne: dla tej samej pary rozmówców Graph oddaje ISTNIEJĄCY czat, nie tworzy
        # drugiego — powtórzenie po timeoucie nie ma skutku ubocznego.
        data = self._post(f"{GRAPH}/chats", payload, retry_transient=True)
        return str(data["id"])

    def _get(self, url: str) -> dict[str, Any]:
        # GET nic nie zmienia → powtórzenie zawsze bezpieczne.
        response = self._request("GET", url, retry_transient=True)
        data: dict[str, Any] = response.json()
        return data

    def _post(
        self, url: str, payload: dict[str, Any], *, retry_transient: bool = False
    ) -> dict[str, Any]:
        """POST domyślnie NIE jest ponawiany na 5xx/timeout — patrz ``_request``.

        Wołający włącza ponawianie tylko tam, gdzie powtórzenie jest udokumentowanie bezpieczne
        (utworzenie czatu 1:1). Wysłanie wiadomości z obrazem NIGDY: powtórka = drugi obraz.
        """
        response = self._request("POST", url, json=payload, retry_transient=retry_transient)
        return response.json() if response.content else {}

    def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        retry_transient: bool = False,
    ) -> httpx.Response:
        """Wykonaj żądanie; ponów 429 ZAWSZE, a 5xx/timeout tylko gdy powtórzenie jest bezpieczne.

        Wzorzec i uzasadnienie jak w ``graph_file_sender._request``: timeout wysyłki znaczy „nie
        wiadomo, czy Graph przyjął" — dla wiadomości z obrazem powtórka = duplikat, dlatego
        ``retry_transient`` włączają tylko ``GET`` i idempotentne utworzenie czatu.
        """
        throttled = 0
        transient = 0
        while True:
            try:
                response = self._client.request(method, url, json=json)
            except httpx.TransportError:
                if not retry_transient or transient >= _MAX_TRANSIENT_RETRIES:
                    raise
                transient += 1
                self._sleep(_TRANSIENT_BACKOFF_S * transient)
                continue
            if response.status_code == 429 and throttled < _MAX_429_RETRIES:
                throttled += 1
                self._sleep(_retry_after(response))
                continue
            if (
                retry_transient
                and response.status_code in _RETRYABLE_STATUS
                and transient < _MAX_TRANSIENT_RETRIES
            ):
                transient += 1
                self._sleep(_TRANSIENT_BACKOFF_S * transient)
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


def _image_message(content: bytes, content_type: str) -> dict[str, Any]:
    """Ciało wiadomości Graph z obrazem INLINE: ``hostedContents`` + ``<img>`` po temporaryId.

    Treść HTML jest w PEŁNI składana tu (sam znacznik ``<img>`` po stałym ``_HOSTED_ID``) — nie ma
    w niej danych od modelu ani od użytkownika, więc nie ma czego escapować. Bajty obrazu idą
    base64 w ``contentBytes``; Graph podmienia ``$value`` na wgraną treść.
    """
    encoded = base64.b64encode(content).decode("ascii")
    return {
        "body": {
            "contentType": "html",
            "content": f'<span><img src="../hostedContents/{_HOSTED_ID}/$value"></span>',
        },
        "hostedContents": [
            {
                "@microsoft.graph.temporaryId": _HOSTED_ID,
                "contentBytes": encoded,
                "contentType": content_type,
            }
        ],
    }


def _retry_after(response: httpx.Response) -> int:
    """Sekundy odczekania z nagłówka Retry-After (fallback, gdy brak/niepoprawny)."""
    try:
        return int(response.headers.get("Retry-After", _DEFAULT_RETRY_AFTER_S))
    except ValueError:
        return _DEFAULT_RETRY_AFTER_S
