"""Testy czystych helperów klienta Graph (bez sieci).

``HttpxGraphChannelClient`` to cienki adapter na ``httpx`` (I/O testowane osobno/smoke),
ale kodowanie share id dla pobrania pliku z SharePoint to czysta, łatwa do pomyłki logika.
"""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from workmate.adapters.inbound.teams_graph.graph import (
    HttpxGraphChannelClient,
    _encode_share_id,
)
from workmate.core.errors import ThreadRootGone


def test_encode_share_id_uses_u_prefix_urlsafe_base64_without_padding():
    url = "https://contoso.sharepoint.com/sites/Team/Shared Documents/General/raport.pdf"

    share_id = _encode_share_id(url)

    assert share_id.startswith("u!")
    body = share_id[2:]
    assert "=" not in body  # dopełnienie usunięte (wymóg Graph)
    assert "+" not in body and "/" not in body  # alfabet urlsafe
    # Dekodowalne z powrotem do oryginalnego URL-a (po uzupełnieniu paddingu).
    padded = body + "=" * (-len(body) % 4)
    assert base64.urlsafe_b64decode(padded).decode("utf-8") == url


def test_post_reply_sends_rendered_html_not_plain_markdown():
    """Egress renderuje Markdown do HTML i wysyła jako ``contentType: html``.

    Bez sieci: ``httpx.MockTransport`` przechwytuje żądanie i pozwala sprawdzić body.
    """
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "r-1"})

    transport = httpx.MockTransport(handler)

    async def run() -> None:
        async with httpx.AsyncClient(transport=transport) as http:
            client = HttpxGraphChannelClient(http, token_provider=lambda: "tok")
            await client.post_reply("team", "chan", "root-1", "**ważne** ustalenie")

    asyncio.run(run())

    body = captured["body"]["body"]  # type: ignore[index]
    assert body["contentType"] == "html"
    assert "<strong>ważne</strong>" in body["content"]
    assert "**" not in body["content"]  # surowy Markdown nie wychodzi dosłownie


def test_list_replies_raises_thread_root_gone_on_404():
    """Root skasowany w Teams → 404. Strona ODCZYTU podnosi ``ThreadRootGone`` (jak strona
    zapisu), żeby poller odróżnił „wątek zniknął" (eksmisja) od awarii odczytu (przejściowa)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "NotFound"}})

    transport = httpx.MockTransport(handler)

    async def run() -> None:
        async with httpx.AsyncClient(transport=transport) as http:
            client = HttpxGraphChannelClient(http, token_provider=lambda: "tok")
            await client.list_replies("team", "chan", "root-9", top=5)

    with pytest.raises(ThreadRootGone, match="root-9"):
        asyncio.run(run())


def test_list_replies_propagates_other_http_errors_as_is():
    """Kontrast: 500 to NIE „wątek zniknął" — leci dalej jako ``HTTPStatusError`` (nie
    ``ThreadRootGone`` ani cichy sukces), więc poller liczy go jako awarię odczytu, nie eksmisję."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"code": "InternalServerError"}})

    transport = httpx.MockTransport(handler)

    async def run() -> None:
        async with httpx.AsyncClient(transport=transport) as http:
            client = HttpxGraphChannelClient(http, token_provider=lambda: "tok")
            await client.list_replies("team", "chan", "root-9", top=5)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(run())


def test_get_hosted_content_falls_back_to_listing_on_404():
    """Body-id z <img> zwraca 404 → listujemy hostedContents i bierzemy autorytatywne id."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/hostedContents/good/$value"):
            return httpx.Response(200, content=b"IMG-BYTES")
        if url.endswith("/hostedContents"):  # listowanie
            return httpx.Response(200, json={"value": [{"id": "bad"}, {"id": "good"}]})
        return httpx.Response(404)  # body-id /$value (i cokolwiek innego)

    transport = httpx.MockTransport(handler)

    async def run() -> bytes:
        async with httpx.AsyncClient(transport=transport) as http:
            client = HttpxGraphChannelClient(http, token_provider=lambda: "tok")
            return await client.get_hosted_content("t", "c", "m", "m", "bad")

    assert asyncio.run(run()) == b"IMG-BYTES"  # „bad" pominięte, „good" pobrane z listowania


def test_get_hosted_content_root_scoped_url_has_no_replies_segment():
    """Post-root (root_id == message_id): URL …/messages/{root}/hostedContents, bez replies."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, content=b"IMG")

    transport = httpx.MockTransport(handler)

    async def run() -> bytes:
        async with httpx.AsyncClient(transport=transport) as http:
            client = HttpxGraphChannelClient(http, token_provider=lambda: "tok")
            return await client.get_hosted_content("t", "c", "root-1", "root-1", "h")

    assert asyncio.run(run()) == b"IMG"
    assert seen["url"].endswith("/messages/root-1/hostedContents/h/$value")
    assert "/replies/" not in seen["url"]


def test_get_hosted_content_reply_scoped_url_uses_replies_segment():
    """Odpowiedź (root_id != message_id): URL zawiera segment …/replies/{reply}/… — fix 404."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, content=b"IMG")

    transport = httpx.MockTransport(handler)

    async def run() -> bytes:
        async with httpx.AsyncClient(transport=transport) as http:
            client = HttpxGraphChannelClient(http, token_provider=lambda: "tok")
            return await client.get_hosted_content("t", "c", "root-1", "reply-9", "h")

    assert asyncio.run(run()) == b"IMG"
    assert seen["url"].endswith("/messages/root-1/replies/reply-9/hostedContents/h/$value")


def test_download_public_url_does_not_follow_redirects(monkeypatch):
    """SSRF: publiczny obraz przekierowujący (np. na metadata) NIE jest podążany — bariera."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def with_mock_transport(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", with_mock_transport)
    client = HttpxGraphChannelClient(httpx.AsyncClient(), token_provider=lambda: "tok")

    async def run() -> None:
        await client.download_public_url("https://media.giphy.com/media/abc/giphy.gif")

    with pytest.raises(RuntimeError):  # przekierowanie → RuntimeError, nie pobranie metadata
        asyncio.run(run())


def test_get_retries_once_after_401_refreshing_token():
    """Przejściowy 401 wymusza JEDNO odświeżenie tokenu i ponowienie — cykl pollingu ocalony.

    Regresja: bez tego każdy przejściowy 401 (Graph przy odświeżaniu tokenu / lag replik)
    propagował z pollera i kosztował jeden zgubiony cykl nasłuchu kanału.
    """
    seen_auth: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("Authorization"))
        if len(seen_auth) == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"value": [{"id": "m-1"}]})

    transport = httpx.MockTransport(handler)

    async def run() -> list[dict[str, object]]:
        async with httpx.AsyncClient(transport=transport) as http:
            client = HttpxGraphChannelClient(http, token_provider=lambda: "swiezy")
            return await client.list_root_messages("t", "c", top=20)

    result = asyncio.run(run())

    assert [m["id"] for m in result] == ["m-1"]
    assert len(seen_auth) == 2  # oryginał + dokładnie jedno ponowienie
    assert seen_auth[1] == "Bearer swiezy"  # token re-wstrzyknięty przez refresh_auth


def test_get_gives_up_on_persistent_401_after_single_refresh():
    """Uporczywy 401 (realny brak uprawnień) propaguje po jednym ponowieniu — bez pętli."""
    calls: list[str] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append("x")
        return httpx.Response(401)

    transport = httpx.MockTransport(handler)

    async def run() -> None:
        async with httpx.AsyncClient(transport=transport) as http:
            client = HttpxGraphChannelClient(http, token_provider=lambda: "tok")
            await client.list_root_messages("t", "c", top=20)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(run())
    assert len(calls) == 2  # oryginał + jedno ponowienie, potem podnosi (nie pętli)
