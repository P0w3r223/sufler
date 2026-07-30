"""Testy wspólnego transportu Jiry (``request_with_retry``) na ``httpx.MockTransport`` — bez sieci.

Sedno (A4): retry na 429 (każda metoda) i 503 (tylko GET — POST nieidempotentny nie jest ponawiany
na 503, żeby nie zdublować zapisu), respektujący ``Retry-After`` (z sufitem, odporny na ujemne
wartości), wykładniczy backoff w jego braku, wyczerpanie prób → propagacja ``HTTPStatusError``,
oraz brak retry na błędach nie-throttlingowych i na sukcesie (``sleep`` nigdy nie wołane).
"""

from __future__ import annotations

import httpx
import pytest

from workmate.adapters.outbound.jira_http import request_with_retry

_URL = "https://jira.example.com/rest/api/2/myself"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_success_returns_without_sleeping():
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    response = request_with_retry(_client(handler), "GET", _URL, sleep=sleeps.append)
    assert response.json() == {"ok": True}
    assert sleeps == []


def test_retries_on_429_with_retry_after_then_succeeds():
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, json={"ok": True})

    response = request_with_retry(_client(handler), "GET", _URL, sleep=sleeps.append)
    assert response.json() == {"ok": True}
    assert calls["n"] == 2
    assert sleeps == [3.0]


def test_retries_on_503_like_429():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    response = request_with_retry(_client(handler), "GET", _URL, sleep=lambda _: None)
    assert response.json() == {"ok": True}
    assert calls["n"] == 2


def test_503_on_post_is_not_retried_unlike_get():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, headers={"Retry-After": "0"})

    with pytest.raises(httpx.HTTPStatusError):
        request_with_retry(_client(handler), "POST", _URL, sleep=lambda _: None)
    assert calls["n"] == 1  # POST nieidempotentny — 503 mógł przyjść PO utworzeniu, brak retry


def test_negative_retry_after_is_clamped_to_zero_not_raised():
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "-5"})
        return httpx.Response(200, json={"ok": True})

    request_with_retry(_client(handler), "GET", _URL, sleep=sleeps.append)
    assert sleeps == [0.0]  # ujemna wartość nie wywraca time.sleep(-x)


def test_retry_after_capped_to_backoff_ceiling():
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "9999"})
        return httpx.Response(200, json={"ok": True})

    request_with_retry(_client(handler), "GET", _URL, sleep=sleeps.append)
    assert sleeps == [60.0]  # sufit _MAX_BACKOFF_S, nie surowe 9999


def test_exponential_backoff_when_no_retry_after_header():
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429)  # brak Retry-After
        return httpx.Response(200, json={"ok": True})

    request_with_retry(_client(handler), "GET", _URL, sleep=sleeps.append)
    assert sleeps == [5.0, 10.0]  # _DEFAULT_RETRY_AFTER_S, potem podwojone


def test_exhausts_retries_then_raises():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "0"})

    with pytest.raises(httpx.HTTPStatusError):
        request_with_retry(_client(handler), "GET", _URL, sleep=lambda _: None)
    assert calls["n"] == 4  # próba początkowa + 3 ponowienia (_MAX_RETRIES)


def test_non_retryable_status_raises_immediately_without_sleep():
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": "zła prośba"})

    with pytest.raises(httpx.HTTPStatusError):
        request_with_retry(_client(handler), "GET", _URL, sleep=sleeps.append)
    assert calls["n"] == 1
    assert sleeps == []


def test_passes_method_params_and_json_body_through():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen["method"] = request.method
        seen["q"] = request.url.params.get("q")
        seen["body"] = _json.loads(request.content) if request.content else None
        return httpx.Response(201, json={"id": "1"})

    request_with_retry(
        _client(handler),
        "POST",
        _URL,
        params={"q": "abc"},
        json={"summary": "test"},
    )
    assert seen == {"method": "POST", "q": "abc", "body": {"summary": "test"}}
