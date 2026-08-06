"""Testy klienta Jira Cloud REST v3 (HttpxJiraCloudClient) na ``httpx.MockTransport`` — bez sieci.

Sedno różnic wobec Server/DC (ADR 0033): nagłówek Basic (email:api_token), konto = ``accountId``
(/rest/api/3/myself), wyszukiwanie ``POST /search/jql`` z paginacją kursorową (``nextPageToken``/
``isLast``, bez ``total``), opis zgłoszenia kodowany/dekodowany jako ADF. Zapis/tranzycja
(ADR 0031/0032) i pola komentarzy zostały USUNIĘTE razem z mostem (ADR 0054) — klient jest dziś
wyłącznie odczytowy, na potrzeby "moich zadań".
"""

from __future__ import annotations

import base64
import json

import httpx

from workmate.adapters.outbound.jira_cloud_api import HttpxJiraCloudClient
from workmate.core.domain.adf import text_to_adf

_BASE = "https://acme.atlassian.net"


def _client(handler) -> HttpxJiraCloudClient:
    transport = httpx.MockTransport(handler)
    return HttpxJiraCloudClient(
        httpx.Client(transport=transport), email="me@example.com", token="api-token", base_url=_BASE
    )


def test_sets_basic_auth_and_reads_account_id():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"accountId": "acc-123", "displayName": "Bot"})

    account = _client(handler).authenticated_account()
    assert account == "acc-123"  # Cloud: accountId (name/key usunięte, RODO)
    assert seen["path"] == "/rest/api/3/myself"
    assert seen["auth"].startswith("Basic ")
    decoded = base64.b64decode(seen["auth"].split(" ", 1)[1]).decode()
    assert decoded == "me@example.com:api-token"


# --- search/jql (paginacja kursorowa, bez total) ----------------------------


def test_search_uses_post_search_jql_with_body():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"issues": [], "isLast": True})

    _client(handler).search_issues("project=WM AND updated>='2026-07-01 10:00'", max_results=25)
    assert seen["method"] == "POST"
    assert seen["path"] == "/rest/api/3/search/jql"
    assert "project=WM" in seen["body"]["jql"]
    assert seen["body"]["expand"] == "changelog"
    assert seen["body"]["maxResults"] == 25
    assert "priority" in seen["body"]["fields"] and "duedate" in seen["body"]["fields"]


def test_search_omits_expand_when_empty():
    """ "Moje zadania" (ADR 0054) nie potrzebuje changelogu — puste ``expand`` nic nie wysyła."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"issues": [], "isLast": True})

    _client(handler).search_issues("project=WM", expand="")
    assert "expand" not in seen["body"]


def test_search_paginates_by_next_page_token():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if "nextPageToken" not in body:
            return httpx.Response(
                200, json={"issues": [{"key": "WM-1"}], "nextPageToken": "tok1", "isLast": False}
            )
        return httpx.Response(200, json={"issues": [{"key": "WM-2"}], "isLast": True})

    issues = _client(handler).search_issues("project=WM")
    assert [i["key"] for i in issues] == ["WM-1", "WM-2"]
    assert len(calls) == 2
    assert calls[1]["nextPageToken"] == "tok1"


def test_search_stops_on_repeated_token_no_infinite_loop():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # Serwer w kółko zwraca ten sam token i isLast=False (znany bug paginacji Cloud).
        return httpx.Response(
            200, json={"issues": [{"key": "WM-1"}], "nextPageToken": "loop", "isLast": False}
        )

    issues = _client(handler).search_issues("project=WM")
    assert calls["n"] == 2  # obrona: powtórzony token przerywa pętlę
    assert len(issues) == 2


def test_search_stops_at_max_pages_with_distinct_tokens():
    # Runaway paginacji z ZA KAŻDYM RAZEM NOWYM tokenem (nie powtórzonym) — obrona repeated-token
    # tu nie zadziała; ratuje twardy cap _MAX_PAGES=10. Bez capu pętla nie skończyłaby się.
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={
                "issues": [{"key": f"WM-{calls['n']}"}],
                "nextPageToken": f"tok-{calls['n']}",  # zawsze inny token
                "isLast": False,
            },
        )

    issues = _client(handler).search_issues("project=WM")
    assert calls["n"] == 10  # twardy cap stron
    assert len(issues) == 10


def test_search_single_page_without_is_last_stops_on_missing_token():
    # Ostatnia strona bez pola ``isLast`` i bez ``nextPageToken`` → stop po jednej stronie (nie
    # zapętla się na braku tokenu). Realny wariant odpowiedzi Cloud dla krótkiego wyniku.
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"issues": [{"key": "WM-1"}]})

    issues = _client(handler).search_issues("project=WM")
    assert calls["n"] == 1
    assert [i["key"] for i in issues] == ["WM-1"]


def test_search_empty_page_with_token_stops_no_extra_call():
    # Pusta strona (issues=[]) mimo tokenu i isLast=False → stop (nie pobieramy w nieskończoność
    # pustych stron). Warunek ``not page`` przerywa pętlę.
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"issues": [], "nextPageToken": "tok1", "isLast": False})

    issues = _client(handler).search_issues("project=WM")
    assert calls["n"] == 1
    assert issues == []


def test_search_flattens_adf_description():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "isLast": True,
                "issues": [{"key": "WM-1", "fields": {"description": text_to_adf("Opis w ADF")}}],
            },
        )

    issues = _client(handler).search_issues("project=WM")
    assert issues[0]["fields"]["description"] == "Opis w ADF"  # spłaszczone do stringa


def test_search_preserves_none_description():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"isLast": True, "issues": [{"key": "WM-1", "fields": {"description": None}}]},
        )

    issues = _client(handler).search_issues("project=WM")
    assert issues[0]["fields"]["description"] is None  # jak Server/DC: brak opisu → None


def test_myself_warns_when_account_timezone_differs_from_host(caplog):
    """JQL bez strefy liczy daty w strefie KONTA — rozjazd przesuwa interpretację dat granicznych"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"accountId": "acc-1", "timeZone": "Pacific/Kiritimati"})

    _client(handler).authenticated_account()

    assert "Pacific/Kiritimati" in caplog.text
