"""Testy adaptera wyjściowego DOKUMENTU 1:1 przez Graph (HttpxGraphUserDocPush, ADR 0027, plik).

Sedno: wysyłka = ``PUT /me/drive/root:/…:/content`` (upload na OneDrive bota) → ``POST …/invite``
(UDZIELENIE dostępu odbiorcy — bez tego karta pliku jest nieotwieralna) → ``GET /me`` (cache id
„głosu" bota) → ``POST /chats`` (idempotentne znalezienie czatu 1:1) → ``POST …/messages`` z
załącznikiem ``reference``. Ponawianie jak w ``graph_file_sender``: 429 zawsze; upload,
invite, GET i utworzenie czatu ponawiane na 5xx (bezpieczne do powtórzenia), ale POST WIADOMOŚCI z
plikiem NIGDY (powtórka po niejednoznacznym timeoucie = druga karta pliku). Testy na
``httpx.MockTransport`` + wstrzykiwanym sleep.
"""

from __future__ import annotations

import json

import httpx
import pytest

from sufler.adapters.outbound.graph_user_doc_push import HttpxGraphUserDocPush
from sufler.core.ports.user_doc_push import UserDocSender

_ME = "bot-me-id"
_TARGET = "u-anna"
_GUID = "2318B4D5-1234-5678-9ABC-DEF012345678"
_ETAG = f'"{{{_GUID}}},1"'
_WEB_URL = "https://contoso-my.sharepoint.com/personal/bot/Documents/raport-ab12cd34.md"
_MD = "text/markdown; charset=utf-8"


def _push(handler, *, token: str = "access-token") -> HttpxGraphUserDocPush:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    # sleep=no-op: backoff jest realny (sekundy), w testach nie odczekujemy.
    return HttpxGraphUserDocPush(client, lambda: token, sleep=lambda _s: None)


def _upload_response() -> httpx.Response:
    # Graph oddaje driveItem z nazwą = wgrany plik (segment ścieżki) — atrapa to odwzorowuje.
    return httpx.Response(
        201,
        json={"id": "item-1", "name": "raport-ab12cd34.md", "webUrl": _WEB_URL, "eTag": _ETAG},
    )


_CONTENT_PATH = "/v1.0/me/drive/root:/Sufler-push/raport-ab12cd34.md:/content"


def _happy(request: httpx.Request) -> httpx.Response:
    """Szczęśliwa ścieżka: folder → upload → invite → /me → /chats → messages (bez błędów)."""
    path = request.url.path
    if path.endswith("/children"):
        return httpx.Response(201, json={"id": "folder-1"})
    if path.endswith("/content"):
        return _upload_response()
    if path.endswith("/invite"):
        return httpx.Response(200, json={"id": "perm-1"})
    if path.endswith("/me"):
        return httpx.Response(200, json={"id": _ME})
    if path.endswith("/chats"):
        return httpx.Response(201, json={"id": "chat-1"})
    return httpx.Response(201, json={"id": "msg-1"})


def _send(push: HttpxGraphUserDocPush) -> None:
    push.send_document_to_user(_TARGET, "raport-ab12cd34.md", b"# Raport", _MD)


# --- sekwencja i kształt żądań --------------------------------------------------


def test_send_ensures_folder_uploads_grants_access_then_posts_message():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return _happy(request)

    _send(_push(handler))

    # Sekwencja jest sednem: folder → upload → invite (dostęp) → /me → czat → wiadomość.
    assert calls == [
        ("POST", "/v1.0/me/drive/root/children"),
        ("PUT", _CONTENT_PATH),
        ("POST", "/v1.0/me/drive/items/item-1/invite"),
        ("GET", "/v1.0/me"),
        ("POST", "/v1.0/chats"),
        ("POST", "/v1.0/chats/chat-1/messages"),
    ]


def test_upload_sends_raw_bytes_with_content_type():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/content"):
            seen["body"] = request.read()
            seen["ctype"] = request.headers.get("Content-Type")
            seen["auth"] = request.headers.get("Authorization")
        return _happy(request)

    _send(_push(handler))

    assert seen["body"] == b"# Raport"  # surowe bajty, nie base64
    assert seen["ctype"] == _MD
    assert seen["auth"] == "Bearer access-token"  # żądanie niesie token (skąd — sonda niżej)


def test_token_is_carried_by_the_request_not_stored_on_the_shared_client():
    """Token doklejamy PER ŻĄDANIE, nie do ``client.headers`` (wzorzec ``graph_thread_source``).

    Sonda wyżej tego NIE rozstrzyga: ``httpx`` scala nagłówki klienta z nagłówkami żądania, więc
    ``request.headers["Authorization"]`` wygląda identycznie przy obu implementacjach. Rozstrzyga
    dopiero token ZMIENNY — przy tokenie ustawionym raz na kliencie wszystkie sześć żądań wysyłki
    jedzie pierwszym, a nagłówek zostaje na kliencie dzielonym z innymi adapterami i pulą wątków.
    Bliźniaczy ``graph_user_push`` ma tę sondę; ten adapter jej nie miał.
    """
    tokeny = iter([f"token-{i}" for i in range(1, 10)])
    autoryzacje: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        autoryzacje.append(request.headers.get("Authorization"))
        return _happy(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    _send(HttpxGraphUserDocPush(client, lambda: next(tokeny), sleep=lambda _s: None))

    # Sześć żądań szczęśliwej ścieżki, każde z WŁASNYM, świeżym tokenem.
    assert autoryzacje == [f"Bearer token-{i}" for i in range(1, 7)]
    assert "Authorization" not in client.headers  # nic nie zostaje na dzielonym kliencie


def test_invite_grants_read_to_the_recipient_by_object_id():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/invite"):
            seen["body"] = request.read().decode()
        return _happy(request)

    _send(_push(handler))

    payload = json.loads(seen["body"])
    # Odbiorca adresowany po AAD id (objectId) — bez maila; tylko odczyt; bez powiadomienia e-mail.
    assert payload["recipients"] == [{"objectId": _TARGET}]
    assert payload["roles"] == ["read"]
    assert payload["requireSignIn"] is True
    assert payload["sendInvitation"] is False


def test_chat_payload_is_one_on_one_with_both_members():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chats"):
            seen["body"] = request.read().decode()
        return _happy(request)

    _send(_push(handler))

    payload = json.loads(seen["body"])
    assert payload["chatType"] == "oneOnOne"
    binds = {m["user@odata.bind"] for m in payload["members"]}
    assert any(_ME in b for b in binds)
    assert any(_TARGET in b for b in binds)


def test_message_payload_references_the_uploaded_file():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            seen["body"] = request.read().decode()
        return _happy(request)

    _send(_push(handler))

    payload = json.loads(seen["body"])
    att = payload["attachments"][0]
    # Załącznik typu reference wiąże driveItem: id = GUID z eTag, contentUrl = webUrl pliku.
    assert att["contentType"] == "reference"
    assert att["id"] == _GUID
    assert att["contentUrl"] == _WEB_URL
    assert att["name"] == "raport-ab12cd34.md"
    # Znacznik <attachment id> MUSI zgadzać się z id z tablicy (inaczej karta nie renderuje).
    assert f'<attachment id="{_GUID}"></attachment>' in payload["body"]["content"]
    assert payload["body"]["contentType"] == "html"


def test_default_caption_names_the_file():
    """Bez ``caption_html`` adapter składa własny minimalny, ESCAPOWANY podpis z nazwą pliku."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            seen["body"] = request.read().decode()
        return _happy(request)

    _send(_push(handler))

    content = json.loads(seen["body"])["body"]["content"]
    assert "<p>W załączniku: raport-ab12cd34.md</p>" in content


def test_caption_html_replaces_the_default_caption_verbatim():
    """A′4: podany ``caption_html`` (zaufany HTML z rdzenia) staje się treścią bez zmian."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            seen["body"] = request.read().decode()
        return _happy(request)

    push = _push(handler)
    caption = "<p>Twoje godziny <b>WT-1</b></p>"
    push.send_document_to_user(_TARGET, "r.md", b"# Raport", _MD, caption_html=caption)

    content = json.loads(seen["body"])["body"]["content"]
    # Załącznik nadal osadzony, ale podpis to nasza bogata treść, a NIE domyślne „W załączniku".
    assert f'<attachment id="{_GUID}"></attachment>' in content
    assert "<p>Twoje godziny <b>WT-1</b></p>" in content
    assert "W załączniku:" not in content


def test_me_id_is_cached_across_sends():
    me_gets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me"):
            me_gets.append("me")
        return _happy(request)

    push = _push(handler)
    _send(push)
    _send(push)

    assert len(me_gets) == 1  # /me raz, mimo dwóch wysyłek


def test_push_folder_is_ensured_once_across_sends():
    """Podfolder zapewniamy raz — drugie wysłanie nie tworzy go ponownie (cache)."""
    folder_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/children"):
            folder_calls.append("children")
        return _happy(request)

    push = _push(handler)
    _send(push)
    _send(push)

    assert len(folder_calls) == 1  # utworzenie folderu raz, mimo dwóch wysyłek


def test_existing_push_folder_409_is_tolerated_and_the_send_completes():
    """Folder już istnieje → Graph zwraca 409, które traktujemy jako sukces (nie replace).

    „Nie rzuca" to za mało: 409 połknięte razem z resztą wysyłki dałoby ten sam wynik testu,
    a odbiorca nie dostałby nic. Sprawdzamy więc, że sekwencja idzie DALEJ — aż do POST-a
    wiadomości z załącznikiem.
    """
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/children"):
            return httpx.Response(409, json={"error": {"code": "nameAlreadyExists"}})
        return _happy(request)

    _send(_push(handler))

    assert calls[0].endswith("/children")  # próba utworzenia była, 409 jej nie zatrzymało
    assert calls[-1] == "/v1.0/chats/chat-1/messages"
    assert _CONTENT_PATH in calls  # plik faktycznie poszedł na dysk, nie tylko wiadomość


# --- niepełna odpowiedź uploadu -------------------------------------------------


def test_upload_without_guid_in_etag_raises_before_send():
    """eTag bez GUID → pliku nie da się załączyć → twardy błąd PRZED invite/wiadomością."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/content"):
            return httpx.Response(201, json={"id": "item-1", "webUrl": _WEB_URL, "eTag": "brak"})
        return _happy(request)

    with pytest.raises(RuntimeError, match="GUID"):
        _send(_push(handler))

    # Folder + upload zdążyły się wykonać — bez martwego udostępnienia/wiadomości.
    assert calls == ["/v1.0/me/drive/root/children", _CONTENT_PATH]


# --- ponawianie: 429 zawsze -----------------------------------------------------


def test_throttling_is_retried_on_message_post():
    """429 = żądanie odrzucone przed przetworzeniem → wolno ponowić także wysyłkę wiadomości."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/messages"):
            return _happy(request)
        attempts.append("msg")
        if len(attempts) < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(201, json={"id": "msg-1"})

    _send(_push(handler))

    assert len(attempts) == 3


# --- ponawianie: 5xx tylko dla bezpiecznych (upload, invite, GET, czat) ---------


def test_upload_is_retried_after_5xx():
    """PUT po ścieżce jest idempotentny (nadpisuje po nazwie) → 5xx wolno ponowić."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/content"):
            attempts.append("put")
            if len(attempts) < 2:
                return httpx.Response(503, json={"error": {"message": "boom"}})
            return _upload_response()
        return _happy(request)

    _send(_push(handler))

    assert len(attempts) == 2


def test_invite_is_retried_after_5xx():
    """Ponowne nadanie tego samego prawa jest nieszkodliwe → 5xx wolno ponowić."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/invite"):
            attempts.append("invite")
            if len(attempts) < 2:
                return httpx.Response(503, json={"error": {"message": "boom"}})
            return httpx.Response(200, json={"id": "perm-1"})
        return _happy(request)

    _send(_push(handler))

    assert len(attempts) == 2


# --- ponawianie: POST wiadomości z plikiem NIGDY (duplikat karty) ---------------


def test_message_post_is_not_retried_after_5xx():
    """SEDNO: powtórzona wiadomość = DRUGA karta pliku w czacie — 5xx wysyłki NIE ponawiamy."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/messages"):
            return _happy(request)
        attempts.append("msg")
        return httpx.Response(503, json={"error": {"message": "boom"}})

    with pytest.raises(httpx.HTTPStatusError):
        _send(_push(handler))

    assert len(attempts) == 1  # ani jednego powtórzenia — inaczej duplikat karty


def test_message_post_is_not_retried_after_timeout():
    """SEDNO: timeout wysyłki jest niejednoznaczny (Graph mógł przyjąć) → powtórka = duplikat."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/messages"):
            return _happy(request)
        attempts.append("msg")
        raise httpx.ReadTimeout("zbyt wolno", request=request)

    with pytest.raises(httpx.ReadTimeout):
        _send(_push(handler))

    assert len(attempts) == 1  # ani jednego powtórzenia


# --- kontrakt portu (strukturalna atrapa w pamięci) -----------------------------


class _FakeUserDocPush:
    """Atrapa ``UserDocSender`` w pamięci — dowód, że port jest implementowalny bez Graph."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, bytes, str, str]] = []

    def send_document_to_user(
        self,
        target_user_id: str,
        filename: str,
        content: bytes,
        content_type: str,
        *,
        caption_html: str = "",
    ) -> None:
        self.sent.append((target_user_id, filename, content, content_type, caption_html))


def test_in_memory_fake_satisfies_the_port():
    sender: UserDocSender = _FakeUserDocPush()  # mypy: atrapa MUSI pasować strukturalnie
    sender.send_document_to_user("u1", "f.md", b"data", _MD, caption_html="<p>hej</p>")

    assert sender.sent[0] == ("u1", "f.md", b"data", _MD, "<p>hej</p>")
