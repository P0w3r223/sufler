"""Testy wspólnego transportu Graph (``graph_http``) — jedno miejsce polityki ponawiania dla
czterech adapterów (``graph_file_sender``, ``graph_user_doc_push``, ``graph_user_push`` — sync;
``graph_teams_notifier`` — async). Adaptery mają własne testy end-to-end przez
``httpx.MockTransport`` (niezmienione tym refaktorem) — tu testujemy logikę decyzji wprost.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from workmate.adapters.outbound import graph_http

_URL = "https://graph.microsoft.com/v1.0/probe"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _async_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- sync: request_with_retry ------------------------------------------------------------


def test_429_honors_retry_after_header():
    waits: list[float] = []
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append("x")
        if len(attempts) < 2:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"ok": True})

    response = graph_http.request_with_retry(_client(handler), "GET", _URL, sleep=waits.append)

    assert response.status_code == 200
    assert waits == [7.0]


def test_429_without_retry_after_uses_default():
    waits: list[float] = []
    attempts: list[str] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append("x")
        if len(attempts) < 2:
            return httpx.Response(429)
        return httpx.Response(200, json={"ok": True})

    graph_http.request_with_retry(_client(handler), "GET", _URL, sleep=waits.append)

    assert waits == [5.0]  # _DEFAULT_RETRY_AFTER_S


def test_429_with_garbage_retry_after_falls_back_to_default():
    waits: list[float] = []
    attempts: list[str] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append("x")
        if len(attempts) < 2:
            return httpx.Response(429, headers={"Retry-After": "nie-liczba"})
        return httpx.Response(200, json={"ok": True})

    graph_http.request_with_retry(_client(handler), "GET", _URL, sleep=waits.append)

    assert waits == [5.0]


def test_429_retried_up_to_five_times_then_gives_up():
    attempts: list[str] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append("x")
        return httpx.Response(429, headers={"Retry-After": "0"})

    with pytest.raises(httpx.HTTPStatusError):
        graph_http.request_with_retry(_client(handler), "GET", _URL, sleep=lambda _s: None)

    assert len(attempts) == 6  # pierwsza próba + 5 ponowień


def test_transient_5xx_not_retried_without_retry_transient():
    """POST bez ``retry_transient`` (domyślnie) NIE ponawia 503 — sedno anty-duplikatu."""
    attempts: list[str] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append("x")
        return httpx.Response(503)

    with pytest.raises(httpx.HTTPStatusError):
        graph_http.request_with_retry(
            _client(handler), "POST", _URL, json={"a": 1}, sleep=lambda _s: None
        )

    assert len(attempts) == 1


def test_transient_5xx_retried_when_retry_transient_true_then_exhausted():
    attempts: list[str] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append("x")
        return httpx.Response(500)

    with pytest.raises(httpx.HTTPStatusError):
        graph_http.request_with_retry(
            _client(handler), "GET", _URL, retry_transient=True, sleep=lambda _s: None
        )

    assert len(attempts) == 4  # pierwsza próba + 3 ponowienia (_MAX_TRANSIENT_RETRIES)


def test_transport_error_not_retried_without_retry_transient():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(httpx.ReadTimeout):
        graph_http.request_with_retry(_client(handler), "POST", _URL, sleep=lambda _s: None)


def test_transport_error_retried_when_retry_transient_true():
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append("x")
        if len(attempts) < 3:
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json={"ok": True})

    response = graph_http.request_with_retry(
        _client(handler), "GET", _URL, retry_transient=True, sleep=lambda _s: None
    )

    assert response.status_code == 200
    assert len(attempts) == 3


# --- async: async_request_with_retry (patrz docstring modułu: sleep przez atrybut asyncio) -----


def _no_sleep(monkeypatch) -> None:
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("workmate.adapters.outbound.graph_http.asyncio.sleep", instant)


def test_async_429_honored_then_succeeds(monkeypatch):
    _no_sleep(monkeypatch)
    attempts: list[str] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append("x")
        if len(attempts) < 2:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"ok": True})

    response = asyncio.run(graph_http.async_request_with_retry(_async_client(handler), "GET", _URL))

    assert response.status_code == 200
    assert len(attempts) == 2


def test_async_post_without_retry_transient_does_not_retry_503(monkeypatch):
    _no_sleep(monkeypatch)
    attempts: list[str] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append("x")
        return httpx.Response(503)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(
            graph_http.async_request_with_retry(_async_client(handler), "POST", _URL, json={"a": 1})
        )

    assert len(attempts) == 1


def test_async_transient_exhausted_raises(monkeypatch):
    _no_sleep(monkeypatch)
    attempts: list[str] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append("x")
        return httpx.Response(500)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(
            graph_http.async_request_with_retry(
                _async_client(handler), "GET", _URL, retry_transient=True
            )
        )

    assert len(attempts) == 4
