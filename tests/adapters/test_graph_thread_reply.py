"""Testy sync postera odpowiedzi tekstowej w wątku (``HttpxGraphThreadReplyPoster``, B3 / ADR 0043).

Bez sieci: ``httpx.MockTransport`` przechwytuje POST do ``.../replies``. Klucz: poprawny URL/treść,
ponawianie 429, 404 na root → ``ThreadRootGone`` (wątek usunięty), inne 4xx/5xx propagują.
"""

from __future__ import annotations

import httpx
import pytest

from workmate.adapters.outbound.graph_thread_reply import HttpxGraphThreadReplyPoster
from workmate.core.errors import ThreadRootGone


def _poster(handler, *, sleeps=None):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HttpxGraphThreadReplyPoster(
        client, lambda: "tok", sleep=(sleeps.append if sleeps is not None else (lambda _s: None))
    )


def test_post_hits_replies_endpoint_with_text_body():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "1"})

    _poster(handler).post("team-1", "chan-1", "root-1", "Notatka złożona.")

    assert seen["url"] == (
        "https://graph.microsoft.com/v1.0/teams/team-1/channels/chan-1/messages/root-1/replies"
    )
    assert seen["auth"] == "Bearer tok"
    assert seen["body"] == {"body": {"contentType": "text", "content": "Notatka złożona."}}


def test_post_retries_on_429_then_succeeds():
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(201, json={"id": "1"})

    _poster(handler, sleeps=sleeps).post("t", "c", "r", "tekst")

    assert calls["n"] == 2  # pierwszy 429, drugi OK
    assert sleeps == [0.0]  # odczekaliśmy wg Retry-After


def test_post_404_on_root_raises_thread_root_gone():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "NotFound"}})

    with pytest.raises(ThreadRootGone):
        _poster(handler).post("t", "c", "root-x", "tekst")


def test_post_other_error_propagates():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "Forbidden"}})

    with pytest.raises(httpx.HTTPStatusError):
        _poster(handler).post("t", "c", "r", "tekst")
