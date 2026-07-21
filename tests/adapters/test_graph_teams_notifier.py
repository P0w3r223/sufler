"""Testy adaptera proaktywnego push do Teams (HttpxTeamsNotifier, ADR 0022) — async MockTransport.

Sedno: 1:1 = /me → utworzenie czatu oneOnOne → wiadomość; kanał = post root; render Markdown→HTML.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from workmate.adapters.outbound.graph_teams_notifier import HttpxTeamsNotifier
from workmate.core.errors import ThreadRootGone


def _notifier(handler) -> HttpxTeamsNotifier:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return HttpxTeamsNotifier(client, lambda: "access-token")


def test_send_chat_creates_oneonone_then_posts_message():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"id": "me-123"})
        if request.url.path == "/v1.0/chats":
            body = request.read().decode()
            assert '"chatType":"oneOnOne"' in body
            assert "me-123" in body and "target-9" in body
            return httpx.Response(201, json={"id": "chat-1"})
        if request.url.path == "/v1.0/chats/chat-1/messages":
            return httpx.Response(201, json={"id": "msg-1"})
        return httpx.Response(404)

    asyncio.run(_notifier(handler).send_chat("target-9", "**cześć**"))
    assert ("GET", "/v1.0/me") in calls
    assert ("POST", "/v1.0/chats") in calls
    assert ("POST", "/v1.0/chats/chat-1/messages") in calls


def test_send_chat_sets_auth_header_and_html_body():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1.0/me":
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={"id": "me-1"})
        if request.url.path == "/v1.0/chats":
            return httpx.Response(201, json={"id": "chat-1"})
        seen["msg_body"] = request.read().decode()
        return httpx.Response(201, json={})

    asyncio.run(_notifier(handler).send_chat("t", "**pogrubienie**"))
    assert seen["auth"] == "Bearer access-token"
    assert '"contentType":"html"' in seen["msg_body"]
    assert "<strong>pogrubienie</strong>" in seen["msg_body"]  # Markdown → HTML


def test_post_channel_posts_root_message():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.read().decode()
        return httpx.Response(201, json={"id": "m-1"})

    asyncio.run(_notifier(handler).post_channel("team-1", "chan-1", "status"))
    assert seen["path"] == "/v1.0/teams/team-1/channels/chan-1/messages"
    assert '"contentType":"html"' in seen["body"]


def test_post_channel_returns_message_id():
    """Root wątku: post_channel zwraca ``id`` z odpowiedzi Graph (do zapamiętania w linku)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": "root-42"})

    root_id = asyncio.run(_notifier(handler).post_channel("team-1", "chan-1", "status"))
    assert root_id == "root-42"


def test_reply_channel_hits_replies_endpoint():
    """Odpowiedź w wątku uderza w ``.../messages/{root_id}/replies`` (dokłada do roota)."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.read().decode()
        return httpx.Response(201, json={"id": "reply-1"})

    asyncio.run(_notifier(handler).reply_channel("team-1", "chan-1", "root-42", "odp"))
    assert seen["path"] == "/v1.0/teams/team-1/channels/chan-1/messages/root-42/replies"
    assert '"contentType":"html"' in seen["body"]


def test_reply_channel_raises_thread_root_gone_on_404():
    """Usunięty root wątku (Graph 404) → ThreadRootGone — notifier utworzy nowy root."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "NotFound"}})

    with pytest.raises(ThreadRootGone):
        asyncio.run(_notifier(handler).reply_channel("t", "c", "root-gone", "odp"))


def test_reply_channel_reraises_non_404_error():
    """Błąd inny niż 404 (np. 500) NIE jest tłumaczony na ThreadRootGone — leci dalej."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(_notifier(handler).reply_channel("t", "c", "root-x", "odp"))


def test_html_escapes_injected_markup():
    """Surowy HTML w treści zdarzenia (z GitHuba) MUSI być escapowany, nie przepuszczony."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read().decode()
        return httpx.Response(201, json={"id": "m-1"})

    asyncio.run(_notifier(handler).post_channel("t", "c", "<script>alert(1)</script>"))
    assert "<script>" not in seen["body"]  # zescapowane (html=False w to_teams_html)


def test_markdown_links_not_clickable_in_push():
    """Link z niezaufanej treści zdarzenia NIE może stać się klikalny (anty-phishing)."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read().decode()
        return httpx.Response(201, json={"id": "m-1"})

    asyncio.run(_notifier(handler).post_channel("t", "c", "[Kliknij](https://evil.example)"))
    # allow_links=False → link schodzi jako tekst, bez żywego <a href>.
    assert "<a " not in seen["body"]
    assert "href" not in seen["body"]


# --- send_chat_html (ADR 0035) — HTML z pominięciem renderera ------------------


def _html_capture(seen: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"id": "me-123"})
        if request.url.path == "/v1.0/chats":
            return httpx.Response(201, json={"id": "chat-1"})
        if request.url.path == "/v1.0/chats/chat-1/messages":
            seen["body"] = request.read().decode()
            return httpx.Response(201, json={"id": "msg-1"})
        return httpx.Response(404)

    return handler


def test_send_chat_html_passes_markup_through_untouched():
    """Sedno ADR 0035: tabela MUSI dotrzeć jako znaczniki, nie jako zescapowany tekst."""
    seen: dict = {}
    table = "<table><tr><td>WT-12</td><td>3.0</td></tr></table>"
    asyncio.run(_notifier(_html_capture(seen)).send_chat_html("target-9", table))
    assert "<table>" in seen["body"]
    assert "&lt;table&gt;" not in seen["body"]


def test_send_chat_html_declares_html_content_type():
    seen: dict = {}
    asyncio.run(_notifier(_html_capture(seen)).send_chat_html("target-9", "<p>x</p>"))
    assert '"contentType":"html"' in seen["body"].replace(" ", "")


def test_send_chat_html_uses_the_same_oneonone_path_as_send_chat():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"id": "me-123"})
        if request.url.path == "/v1.0/chats":
            assert '"chatType":"oneOnOne"' in request.read().decode()
            return httpx.Response(201, json={"id": "chat-1"})
        return httpx.Response(201, json={"id": "msg-1"})

    asyncio.run(_notifier(handler).send_chat_html("target-9", "<p>x</p>"))
    assert calls == ["/v1.0/me", "/v1.0/chats", "/v1.0/chats/chat-1/messages"]


def test_send_chat_still_escapes_raw_html():
    """Regresja: nowa metoda NIE MOŻE rozluźnić starej ścieżki dla treści niezaufanej."""
    seen: dict = {}
    asyncio.run(_notifier(_html_capture(seen)).send_chat("target-9", "<script>alert(1)</script>"))
    assert "<script>" not in seen["body"]
