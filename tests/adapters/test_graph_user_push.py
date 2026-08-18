"""Testy adaptera wyjściowego obrazu 1:1 przez Graph (HttpxGraphUserImagePush, ADR 0027, A′3).

Sedno: wysyłka = ``GET /me`` (cache id „głosu" bota) → ``POST /chats`` (idempotentne utworzenie/
znalezienie czatu 1:1) → ``POST …/messages`` z obrazem INLINE w ``hostedContents`` (bez dysku
SharePoint). Ponawianie jak w ``graph_file_sender``: 429 zawsze; GET oraz utworzenie czatu ponawiane
na 5xx/timeout (bezpieczne do powtórzenia), ale POST WIADOMOŚCI z obrazem NIGDY (powtórka po
niejednoznacznym timeoucie = drugi obraz). Testy na ``httpx.MockTransport`` + wstrzykiwanym sleep.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from workmate.adapters.outbound.graph_user_push import HttpxGraphUserImagePush
from workmate.core.ports.user_push import UserImageSender

_ME = "bot-me-id"
_TARGET = "u-anna"


def _push(handler, *, token: str = "access-token") -> HttpxGraphUserImagePush:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    # sleep=no-op: backoff jest realny (sekundy), w testach nie odczekujemy.
    return HttpxGraphUserImagePush(client, lambda: token, sleep=lambda _s: None)


def _happy(request: httpx.Request) -> httpx.Response:
    """Szczęśliwa ścieżka: /me → /chats → messages (bez błędów)."""
    if request.url.path.endswith("/me"):
        return httpx.Response(200, json={"id": _ME})
    if request.url.path.endswith("/chats"):
        return httpx.Response(201, json={"id": "chat-1"})
    return httpx.Response(201, json={"id": "msg-1"})


# --- sekwencja i kształt żądań --------------------------------------------------


def test_send_gets_me_creates_chat_then_posts_message():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return _happy(request)

    _push(handler).send_image_to_user(_TARGET, b"PNGDATA", "image/png")

    # Sekwencja jest sednem metody: NAJPIERW /me, potem utworzenie czatu, na końcu wiadomość.
    assert calls == [
        ("GET", "/v1.0/me"),
        ("POST", "/v1.0/chats"),
        ("POST", "/v1.0/chats/chat-1/messages"),
    ]


def test_me_id_is_cached_across_sends():
    """Drugie wysłanie nie odpytuje ponownie ``/me`` — id „głosu" bota jest cache'owane."""
    me_gets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me"):
            me_gets.append("me")
        return _happy(request)

    push = _push(handler)
    push.send_image_to_user(_TARGET, b"A", "image/png")
    push.send_image_to_user(_TARGET, b"B", "image/gif")

    assert len(me_gets) == 1  # /me raz, mimo dwóch wysyłek


def test_chat_payload_is_one_on_one_with_both_members():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chats"):
            seen["body"] = request.read().decode()
        return _happy(request)

    _push(handler).send_image_to_user(_TARGET, b"x", "image/png")

    payload = json.loads(seen["body"])
    assert payload["chatType"] == "oneOnOne"
    binds = {m["user@odata.bind"] for m in payload["members"]}
    # Oba końce czatu adresowane po AAD id: „głos" bota z /me ORAZ pre-związany odbiorca.
    assert any(_ME in b for b in binds)
    assert any(_TARGET in b for b in binds)


def test_message_payload_embeds_image_inline_via_hosted_contents():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            seen["body"] = request.read().decode()
            seen["auth"] = request.headers.get("Authorization")
        return _happy(request)

    _push(handler).send_image_to_user(_TARGET, b"PNGDATA", "image/png")

    payload = json.loads(seen["body"])
    hosted = payload["hostedContents"][0]
    # Bajty idą base64 w contentBytes; contentType z wywołania; temporaryId spina obraz z <img>.
    assert hosted["contentBytes"] == base64.b64encode(b"PNGDATA").decode("ascii")
    assert hosted["contentType"] == "image/png"
    temp_id = hosted["@microsoft.graph.temporaryId"]
    assert payload["body"]["contentType"] == "html"
    assert f'src="../hostedContents/{temp_id}/$value"' in payload["body"]["content"]
    assert seen["auth"] == "Bearer access-token"  # _refresh_auth ustawia token przed wysyłką


# --- ponawianie: 429 zawsze -----------------------------------------------------


def test_throttling_is_retried_on_message_post():
    """429 = żądanie odrzucone przed przetworzeniem → wolno ponowić także wysyłkę wiadomości."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": _ME})
        if request.url.path.endswith("/chats"):
            return httpx.Response(201, json={"id": "chat-1"})
        attempts.append("msg")
        if len(attempts) < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(201, json={"id": "msg-1"})

    _push(handler).send_image_to_user(_TARGET, b"x", "image/png")

    assert len(attempts) == 3


# --- ponawianie: 5xx/timeout tylko dla bezpiecznych (GET, utworzenie czatu) ------


def test_me_get_is_retried_after_5xx():
    """GET nic nie zmienia → 5xx wolno ponowić."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me"):
            attempts.append("me")
            if len(attempts) < 2:
                return httpx.Response(503, json={"error": {"message": "boom"}})
            return httpx.Response(200, json={"id": _ME})
        return _happy(request)

    _push(handler).send_image_to_user(_TARGET, b"x", "image/png")

    assert len(attempts) == 2


def test_chat_create_is_retried_after_5xx():
    """Utworzenie czatu 1:1 jest idempotentne (Graph oddaje istniejący) → 5xx wolno ponowić."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": _ME})
        if request.url.path.endswith("/chats"):
            attempts.append("chats")
            if len(attempts) < 2:
                return httpx.Response(503, json={"error": {"message": "boom"}})
            return httpx.Response(201, json={"id": "chat-1"})
        return httpx.Response(201, json={"id": "msg-1"})

    _push(handler).send_image_to_user(_TARGET, b"x", "image/png")

    assert len(attempts) == 2


def test_chat_create_transient_retries_give_up_instead_of_hanging():
    """Sufit prób jest twardy także tam, gdzie ponawiamy — nie wisimy na trwałym 5xx."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": _ME})
        attempts.append("chats")
        return httpx.Response(503, json={"error": {"message": "boom"}})

    with pytest.raises(httpx.HTTPStatusError):
        _push(handler).send_image_to_user(_TARGET, b"x", "image/png")

    assert len(attempts) == 4  # pierwsza próba + trzy ponowienia (_MAX_TRANSIENT_RETRIES)


# --- ponawianie: POST wiadomości z obrazem NIGDY (duplikat obrazu) --------------


def test_message_post_is_not_retried_after_5xx():
    """SEDNO: powtórzona wiadomość = DRUGI obraz w czacie — 5xx wysyłki NIE ponawiamy."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": _ME})
        if request.url.path.endswith("/chats"):
            return httpx.Response(201, json={"id": "chat-1"})
        attempts.append("msg")
        return httpx.Response(503, json={"error": {"message": "boom"}})

    with pytest.raises(httpx.HTTPStatusError):
        _push(handler).send_image_to_user(_TARGET, b"x", "image/png")

    assert len(attempts) == 1  # ani jednego powtórzenia — inaczej duplikat obrazu


def test_message_post_is_not_retried_after_timeout():
    """SEDNO: timeout wysyłki jest niejednoznaczny (Graph mógł przyjąć) → powtórka = duplikat."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": _ME})
        if request.url.path.endswith("/chats"):
            return httpx.Response(201, json={"id": "chat-1"})
        attempts.append("msg")
        raise httpx.ReadTimeout("zbyt wolno", request=request)

    with pytest.raises(httpx.ReadTimeout):
        _push(handler).send_image_to_user(_TARGET, b"x", "image/png")

    assert len(attempts) == 1  # ani jednego powtórzenia


# --- kontrakt portu (strukturalna atrapa w pamięci) -----------------------------


class _FakeUserImagePush:
    """Atrapa ``UserImageSender`` w pamięci — dowód, że port jest implementowalny bez Graph."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, str]] = []

    def send_image_to_user(self, target_user_id: str, content: bytes, content_type: str) -> None:
        self.sent.append((target_user_id, content, content_type))


def test_in_memory_fake_satisfies_the_port():
    sender: UserImageSender = _FakeUserImagePush()  # mypy: atrapa MUSI pasować strukturalnie
    sender.send_image_to_user("u1", b"data", "image/png")

    assert sender.sent[0] == ("u1", b"data", "image/png")


def test_token_is_carried_by_the_request_not_stored_on_the_shared_client():
    """Token doklejamy PER ŻĄDANIE (wzorzec ``graph_thread_source``), nie do ``client.headers``.

    ``httpx.Client`` bywa dzielony (jedna instancja na proces, wiele adapterów) i wołany z puli
    wątków — token wstrzyknięty w obiekt klienta jest wtedy stanem widocznym dla cudzych żądań,
    a jego świeżość zależy od kolejności wywołań. Każde żądanie ma nieść SWÓJ token.
    """
    tokens = iter(["token-1", "token-2", "token-3"])
    autoryzacje: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        autoryzacje.append(request.headers.get("Authorization"))
        return _happy(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    push = HttpxGraphUserImagePush(client, lambda: next(tokens), sleep=lambda _s: None)

    push.send_image_to_user(_TARGET, b"PNGDATA", "image/png")

    assert autoryzacje == ["Bearer token-1", "Bearer token-2", "Bearer token-3"]
    assert "Authorization" not in client.headers  # nic nie zostaje na kliencie
