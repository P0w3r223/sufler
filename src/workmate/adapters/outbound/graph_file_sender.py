"""Wyjściowy załącznik Graph przez Microsoft Graph (ADR 0026) — implementacja ``TeamsFileSender``.

Importowany LENIWIE (w wiringu drzwi), bo wymaga ``httpx``. SYNCHRONICZNY (``httpx.Client``, nie
``AsyncClient``): narzędzia agenta biegną synchronicznie w puli wątków, więc ścieżka pliku wygląda
jak zapis GitHub Gate-4 (``HttpxGithubClient``), nie jak async poller. Token MSAL (delegowany, ten
sam co ``teams_graph``) jest synchroniczny — wołamy go wprost, bez puli wątków.

Wgranie pliku to DWA żądania Graph: (1) ``GET …/filesFolder`` po ``driveId`` folderu kanału,
(2) ``PUT …/content`` z surowymi bajtami. Załączenie w wątku to trzecie żądanie POST, w którym
tablica ``attachments`` (typ ``reference`` → driveItem w SharePoint) jest wiązana z treścią przez
znacznik ``<attachment id="GUID">`` — a ``GUID`` bierze się z ``eTag`` wgranego pliku.

Retry (429 zawsze, 5xx/timeout tylko dla żądań bezpiecznych do powtórzenia — odczyt folderu i
idempotentny, po ścieżce, upload TAK, ale WYSŁANIE ODPOWIEDZI z plikiem NIE: powtórka po
niejednoznacznym timeoucie dołożyłaby drugi załącznik do wątku) idzie przez wspólny
``graph_http.request_with_retry`` — ta sama polityka co w ``graph_teams_notifier``/
``graph_user_push``/``graph_user_doc_push``, jedno miejsce zamiast czterech kopii.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from workmate.adapters.outbound import graph_http
from workmate.core.errors import ThreadRootGone
from workmate.core.ports.file_output import UploadedFile

GRAPH = "https://graph.microsoft.com/v1.0"
# GUID z ``eTag`` driveItem (np. ``"{2318B4D5-…},1"``) — Teams wiąże nim znacznik
# ``<attachment id>`` w treści z pozycją w tablicy ``attachments``. Szukamy wzorca UUID w eTag.
_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


class HttpxGraphFileSender:
    """``TeamsFileSender`` na Graph (SYNC): upload na dysk kanału + załącznik w wątku."""

    def __init__(
        self,
        client: httpx.Client,
        token_provider: Callable[[], str],
        *,
        sleep: Callable[[float], None] = time.sleep,
        retry_transient: bool = True,
    ) -> None:
        self._client = client
        self._token = token_provider
        self._sleep = sleep  # wstrzykiwalny, by testy nie odczekiwały realnego backoffu
        # Ponawianie 5xx/timeoutów wyłączają konsumenci, KTÓRZY MAJĄ WŁASNĄ pętlę ponowień —
        # dziś skrzynka nadawcza (plik zostaje, następna tura ponawia). Dwie warstwy retry nad tą
        # samą operacją mnożą najgorszy przypadek: 4 próby × timeout × dwa żądania, a to opóźnienie
        # płaci rozmówca czekający na odpowiedź tury. 429 zostaje ponawiane niezależnie od tej
        # flagi (patrz ``graph_http``) — tam odczekanie jest wymogiem Graph, nie wyborem.
        self._retry_transient = retry_transient

    def upload_channel_file(
        self,
        team_id: str,
        channel_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> UploadedFile:
        """Wgraj bajty na dysk kanału (SharePoint) i zwróć referencję do załączenia."""
        self._refresh_auth()
        folder = self._get(f"{GRAPH}/teams/{team_id}/channels/{channel_id}/filesFolder")
        drive_id = _require(folder.get("parentReference", {}).get("driveId"), "filesFolder.driveId")
        folder_id = _require(folder.get("id"), "filesFolder.id")
        # Upload prosty (PUT po ścieżce) — idempotentny: ta sama nazwa nadpisuje, nie duplikuje.
        item = self._put_bytes(
            f"{GRAPH}/drives/{drive_id}/items/{folder_id}:/{quote(filename, safe='')}:/content",
            content,
            content_type,
        )
        # web_url i attachment_id są load-bearing (→ contentUrl i <attachment id>): niepełna
        # odpowiedź PUT musi paść tu, a nie zbudować martwy załącznik w dół.
        attachment_id = _attachment_guid(str(item.get("eTag", "")))
        if not attachment_id:
            raise RuntimeError(
                "Odpowiedź uploadu Graph nie ma GUID w eTag — pliku nie da się załączyć."
            )
        return UploadedFile(
            item_id=str(item.get("id", "")),
            name=str(item.get("name", filename)),
            web_url=_require(item.get("webUrl"), "upload.webUrl"),
            attachment_id=attachment_id,
        )

    def post_reply_with_attachment(
        self,
        team_id: str,
        channel_id: str,
        root_id: str,
        html: str,
        attachment: UploadedFile,
    ) -> None:
        """Wyślij odpowiedź w wątku z załączonym plikiem; 404 na root → ``ThreadRootGone``."""
        self._refresh_auth()
        att_id = attachment.attachment_id  # GUID zwalidowany już przy wgraniu (patrz upload)
        payload: dict[str, Any] = {
            "body": {
                "contentType": "html",
                # Znacznik <attachment> wiąże treść z pozycją w tablicy; ``html`` (zaufany,
                # z rdzenia) idzie po nim jako opis.
                "content": f'<attachment id="{att_id}"></attachment>{html}',
            },
            "attachments": [
                {
                    "id": att_id,
                    "contentType": "reference",
                    "contentUrl": attachment.web_url,
                    "name": attachment.name,
                }
            ],
        }
        url = f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages/{root_id}/replies"
        try:
            self._post(url, payload)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ThreadRootGone(f"root wątku {root_id} nie istnieje") from exc
            raise

    def _refresh_auth(self) -> None:
        """Ustaw nagłówek Authorization świeżym tokenem (sync MSAL, cichy refresh z cache)."""
        self._client.headers["Authorization"] = f"Bearer {self._token()}"

    def _get(self, url: str) -> dict[str, Any]:
        # GET nic nie zmienia → powtórzenie zawsze bezpieczne (o ile wołający go chce).
        response = self._request("GET", url, retry_transient=self._retry_transient)
        data: dict[str, Any] = response.json()
        return data

    def _put_bytes(self, url: str, content: bytes, content_type: str) -> dict[str, Any]:
        # PUT po ścieżce jest idempotentny (nadpisuje po nazwie) → wolno ponawiać przy 5xx.
        response = self._request(
            "PUT",
            url,
            content=content,
            headers={"Content-Type": content_type},
            retry_transient=self._retry_transient,
        )
        return response.json() if response.content else {}

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        # Wysłanie wiadomości NIE jest ponawiane: powtórka po timeoucie dołożyłaby drugi załącznik.
        response = self._request("POST", url, json=payload, retry_transient=False)
        return response.json() if response.content else {}

    def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        retry_transient: bool = False,
    ) -> httpx.Response:
        """Wykonaj żądanie ze wspólną polityką ponawiania — patrz ``graph_http``."""
        return graph_http.request_with_retry(
            self._client,
            method,
            url,
            json=json,
            content=content,
            headers=headers,
            retry_transient=retry_transient,
            sleep=self._sleep,
        )


def _attachment_guid(etag: str) -> str:
    """Wyłuskaj GUID z ``eTag`` driveItem (Teams wymaga go jako ``attachment.id``); '' gdy brak."""
    match = _GUID_RE.search(etag)
    return match.group(0) if match else ""


def _require(value: Any, name: str) -> str:
    """Niepuste pole odpowiedzi Graph albo czytelny błąd (kanał bez folderu plików)."""
    if not value:
        raise RuntimeError(f"Odpowiedź Graph nie zawiera '{name}' — kanał bez dysku plików?")
    return str(value)
