"""Wyjściowy DOKUMENT 1:1 przez Microsoft Graph (ADR 0027, wariant plikowy) — ``UserDocSender``.

Importowany LENIWIE (w wiringu drzwi), bo wymaga ``httpx``. SYNCHRONICZNY (``httpx.Client``): jak
plik w wątku (``HttpxGraphFileSender``) i obraz 1:1 (``HttpxGraphUserImagePush``) — narzędzia agenta
biegną synchronicznie w puli wątków. Token MSAL (delegowany, ten sam co ``teams_graph``) jest
synchroniczny — wołamy go wprost.

Czat 1:1 NIE ma dysku SharePoint (jak kanał), więc plik ląduje na OneDrive „głosu" bota. Sekwencja
(patrz ``docs/research/graph-1to1-chat-file-attachment.md``):

0. ``POST /me/drive/root/children`` — idempotentne zapewnienie dedykowanego podfolderu (409 = już
   jest → OK), żeby pushowane pliki nie zaśmiecały roota (cache po pierwszym razie).
1. ``PUT /me/drive/root:/{folder}/{nazwa}:/content`` — upload na OneDrive (idempotentny po ścieżce;
   zwraca ``eTag`` → GUID załącznika oraz ``webUrl`` → ``contentUrl``).
2. ``POST /me/drive/items/{id}/invite`` — UDZIELENIE dostępu odbiorcy (``objectId`` = jego AAD id).
   BEZ tego kroku odbiorca dostaje kartę pliku, której NIE otworzy (dysk bota jest prywatny) — to
   kluczowa różnica wobec pliku na kanale, gdzie członkostwo dziedziczy się z biblioteki.
3. ``POST /chats`` — idempotentne znalezienie/utworzenie czatu 1:1 z odbiorcą.
4. ``POST /chats/{id}/messages`` — wiadomość z załącznikiem ``reference`` (``<attachment id>``).

Retry (429 ZAWSZE; 5xx/timeout tylko dla bezpiecznych — upload po ścieżce, ``invite`` ponowne
nadanie nieszkodliwe i utworzenie czatu TAK, ale WYSŁANIE WIADOMOŚCI z plikiem NIE, powtórka po
niejednoznacznym timeoucie = druga karta pliku) idzie przez wspólny
``graph_http.request_with_retry`` — ta sama polityka co ``graph_file_sender``/
``graph_teams_notifier``/``graph_user_push``, jedno miejsce zamiast czterech kopii.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from html import escape
from typing import Any
from urllib.parse import quote

import httpx

from workmate.adapters.outbound import graph_http

GRAPH = "https://graph.microsoft.com/v1.0"
# GUID z ``eTag`` driveItem (np. ``"{2318B4D5-…},1"``) — Teams wiąże nim ``<attachment id>``
# w treści z pozycją w tablicy ``attachments`` (jak ``graph_file_sender``).
_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
# Dedykowany podfolder na OneDrive bota — pushowane dokumenty NIE zaśmiecają roota i są odróżnialne
# od reszty dysku (higiena operacyjna; TTL-cleanup odłożony na pilotaż, ADR 0027). Tworzony
# idempotentnie (409 = już istnieje → OK), cache po pierwszym utworzeniu.
_PUSH_FOLDER = "WorkMate-push"


class HttpxGraphUserDocPush:
    """``UserDocSender`` (SYNC): upload OneDrive + udostępnienie + załącznik w czacie 1:1."""

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
        self._folder_ready = False  # czy podfolder push-u już zapewniony — cache po pierwszym

    def send_document_to_user(
        self,
        target_user_id: str,
        filename: str,
        content: bytes,
        content_type: str,
        *,
        caption_html: str = "",
    ) -> None:
        """Wgraj ``content`` na OneDrive, udostępnij odbiorcy i wyślij jako załącznik w czacie 1:1.

        ``caption_html`` (opcjonalny) staje się TREŚCIĄ wiadomości niosącej załącznik — bez zmian,
        jak ``send_chat_html`` (kontrakt: to zaufany HTML z rdzenia). Pusty = adapter składa własny
        minimalny podpis. POST wiadomości NIE jest ponawiany — powtórka po niejednoznacznym
        timeoucie = druga karta.
        """
        self._refresh_auth()
        self._ensure_push_folder()
        item = self._upload_to_onedrive(filename, content, content_type)
        attachment_id = _attachment_guid(str(item.get("eTag", "")))
        if not attachment_id:
            raise RuntimeError(
                "Odpowiedź uploadu Graph nie ma GUID w eTag — pliku nie da się załączyć."
            )
        item_id = _require(item.get("id"), "upload.id")
        web_url = _require(item.get("webUrl"), "upload.webUrl")
        name = str(item.get("name", filename))
        # Bez tego odbiorca nie otworzy pliku (dysk bota jest prywatny). objectId = AAD id nadawcy.
        self._grant_access(item_id, target_user_id)
        me_id = self._me_id_cached()
        chat_id = self._create_or_get_chat(me_id, target_user_id)
        self._post(
            f"{GRAPH}/chats/{chat_id}/messages",
            _attachment_message(attachment_id, web_url, name, caption_html),
        )

    def _ensure_push_folder(self) -> None:
        """Zapewnij dedykowany podfolder na OneDrive bota (idempotentnie, cache po pierwszym).

        ``conflictBehavior: fail`` → gdy folder już istnieje, Graph zwraca 409 — traktujemy jako
        sukces (nie ``replace`` — ten skasowałby zawartość). Tworzenie z ``fail`` jest w efekcie
        idempotentne (powtórka po timeoucie → 409 → OK), więc wolno ponawiać na 5xx.
        """
        if self._folder_ready:
            return
        payload = {
            "name": _PUSH_FOLDER,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "fail",
        }
        try:
            self._post(f"{GRAPH}/me/drive/root/children", payload, retry_transient=True)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:  # 409 = folder już istnieje → to jest OK
                raise
        self._folder_ready = True

    def _upload_to_onedrive(
        self, filename: str, content: bytes, content_type: str
    ) -> dict[str, Any]:
        """Upload prosty do podfolderu na OneDrive bota (PUT po ścieżce — idempotentny po nazwie).

        Nazwa jest już bezpieczna i UNIKALNA-PO-TREŚCI (``_safe_doc_name`` w rdzeniu), więc ta sama
        treść trafia w ten sam item (bez duplikatu), a różna — w osobny. ``quote(safe='')`` domyka
        wstrzyknięcie ścieżki niezależnie od sluggowania po stronie rdzenia (obrona w głąb).
        """
        return self._put_bytes(
            f"{GRAPH}/me/drive/root:/{_PUSH_FOLDER}/{quote(filename, safe='')}:/content",
            content,
            content_type,
        )

    def _grant_access(self, item_id: str, target_user_id: str) -> None:
        """Nadaj odbiorcy prawo ODCZYTU pliku (``invite`` po ``objectId``); ponowne = nieszkodliwe.

        ``requireSignIn`` (dostęp tylko dla zalogowanego użytkownika tenanta) + ``sendInvitation:
        false`` (bez maila — dostawą jest karta w czacie, nie powiadomienie e-mail).
        """
        payload = {
            "recipients": [{"objectId": target_user_id}],
            "requireSignIn": True,
            "sendInvitation": False,
            "roles": ["read"],
        }
        # Idempotentne: ponowne nadanie tego samego prawa jest nieszkodliwe → wolno ponawiać na 5xx.
        self._post(f"{GRAPH}/me/drive/items/{item_id}/invite", payload, retry_transient=True)

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
        # Idempotentne: dla tej samej pary Graph oddaje ISTNIEJĄCY czat, nie tworzy drugiego.
        data = self._post(f"{GRAPH}/chats", payload, retry_transient=True)
        return str(data["id"])

    def _get(self, url: str) -> dict[str, Any]:
        # GET nic nie zmienia → powtórzenie zawsze bezpieczne.
        response = self._request("GET", url, retry_transient=True)
        data: dict[str, Any] = response.json()
        return data

    def _put_bytes(self, url: str, content: bytes, content_type: str) -> dict[str, Any]:
        # PUT po ścieżce jest idempotentny (nadpisuje po nazwie) → wolno ponawiać przy 5xx.
        response = self._request(
            "PUT",
            url,
            content=content,
            headers={"Content-Type": content_type},
            retry_transient=True,
        )
        return response.json() if response.content else {}

    def _post(
        self, url: str, payload: dict[str, Any], *, retry_transient: bool = False
    ) -> dict[str, Any]:
        """POST domyślnie NIE jest ponawiany na 5xx/timeout — patrz ``_request``.

        Wołający włącza ponawianie tylko tam, gdzie powtórzenie jest udokumentowanie bezpieczne
        (nadanie dostępu, utworzenie czatu). Wysłanie wiadomości z plikiem NIGDY: powtórka = druga
        karta pliku.
        """
        response = self._request("POST", url, json=payload, retry_transient=retry_transient)
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


def _member(user_id: str) -> dict[str, Any]:
    """Członek czatu 1:1 adresowany przez AAD user id (wzorzec Powiadomienia_teams)."""
    return {
        "@odata.type": "#microsoft.graph.aadUserConversationMember",
        "roles": ["owner"],
        "user@odata.bind": f"{GRAPH}/users('{user_id}')",
    }


def _attachment_message(
    attachment_id: str, web_url: str, name: str, caption_html: str = ""
) -> dict[str, Any]:
    """Ciało wiadomości Graph z załącznikiem ``reference`` (driveItem na OneDrive).

    Znacznik ``<attachment id="GUID">`` wiąże treść z pozycją w tablicy ``attachments`` (renderuje
    kartę pliku). Podpis domyślny jest w PEŁNI składany tu i ESCAPOWANY — ``name`` to bezpieczny
    slug z rdzenia, ale escapujemy w głąb (obrona przed HTML w nazwie). ``caption_html`` (gdy
    podany) ZASTĘPUJE domyślny podpis i idzie BEZ escapowania — to zaufany HTML z rdzenia (kontrakt
    ``UserDocSender.send_document_to_user``). ``contentUrl`` = ``webUrl`` pliku (to, co osadza sam
    Teams; ``webDavUrl`` jako fallback do live-smoke — patrz research-doc).
    """
    body = caption_html or f"<p>W załączniku: {escape(name)}</p>"
    return {
        "body": {
            "contentType": "html",
            "content": f'<attachment id="{attachment_id}"></attachment>{body}',
        },
        "attachments": [
            {
                "id": attachment_id,
                "contentType": "reference",
                "contentUrl": web_url,
                "name": name,
            }
        ],
    }


def _attachment_guid(etag: str) -> str:
    """Wyłuskaj GUID z ``eTag`` driveItem (Teams wymaga go jako ``attachment.id``); '' gdy brak."""
    match = _GUID_RE.search(etag)
    return match.group(0) if match else ""


def _require(value: Any, name: str) -> str:
    """Niepuste pole odpowiedzi Graph albo czytelny błąd (niepełna odpowiedź uploadu)."""
    if not value:
        raise RuntimeError(f"Odpowiedź Graph nie zawiera '{name}' — upload niekompletny?")
    return str(value)
