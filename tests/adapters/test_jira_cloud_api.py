"""Testy klienta Jira Cloud REST v3 (HttpxJiraCloudClient) na ``httpx.MockTransport`` — bez sieci.

Sedno różnic wobec Server/DC (ADR 0033): nagłówek Basic (email:api_token), konto = ``accountId``
(/rest/api/3/myself), wyszukiwanie ``POST /search/jql`` z paginacją kursorową (``nextPageToken``/
``isLast``, bez ``total``), treść ``description``/``comment.body`` kodowana/dekodowana jako ADF.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from workmate.adapters.outbound.jira_cloud_api import HttpxJiraCloudClient
from workmate.core.domain.adf import adf_to_text, text_to_adf
from workmate.core.errors import WriteError

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
    assert "comment" in seen["body"]["fields"]  # komentarze inline


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


def test_search_flattens_adf_description_and_comment_bodies():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "isLast": True,
                "issues": [
                    {
                        "key": "WM-1",
                        "fields": {
                            "description": text_to_adf("Opis w ADF"),
                            "comment": {
                                "comments": [{"id": "1", "body": text_to_adf("komentarz ADF")}]
                            },
                        },
                    }
                ],
            },
        )

    issues = _client(handler).search_issues("project=WM")
    fields = issues[0]["fields"]
    assert fields["description"] == "Opis w ADF"  # spłaszczone do stringa
    assert fields["comment"]["comments"][0]["body"] == "komentarz ADF"


def test_search_preserves_none_description():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"isLast": True, "issues": [{"key": "WM-1", "fields": {"description": None}}]},
        )

    issues = _client(handler).search_issues("project=WM")
    assert issues[0]["fields"]["description"] is None  # jak Server/DC: brak opisu → None


def test_search_tolerates_comment_without_body_and_non_dict_entries():
    # Odporność spłaszczania: komentarz BEZ ``body`` (Cloud potrafi go pominąć) oraz nie-dict wpis
    # nie mogą wywrócić klienta AttributeError/KeyError — flatten pomija je, resztę tłumaczy.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "isLast": True,
                "issues": [
                    {
                        "key": "WM-1",
                        "fields": {
                            "comment": {
                                "comments": [
                                    {"id": "1"},  # brak ``body`` — nie tykamy
                                    "śmieć",  # nie-dict — pomijany
                                    {"id": "2", "body": text_to_adf("realny komentarz")},
                                ]
                            }
                        },
                    }
                ],
            },
        )

    issues = _client(handler).search_issues("project=WM")
    comments = issues[0]["fields"]["comment"]["comments"]
    assert "body" not in comments[0]  # nietknięty (brak body)
    assert comments[2]["body"] == "realny komentarz"  # spłaszczony do tekstu


# --- write (ADR 0031) — ADF encode ------------------------------------------


def test_create_issue_encodes_description_as_adf_and_fetches_created():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.url.path == "/rest/api/3/issue"
            seen["payload"] = json.loads(request.content)
            return httpx.Response(201, json={"key": "WM-9"})
        seen["get_fields"] = request.url.params.get("fields")
        return httpx.Response(200, json={"fields": {"created": "2026-07-15T10:00:00.000+0200"}})

    result = _client(handler).create_issue("WM", "Task", "Tytuł", "Opis", ("pilne",))
    assert result["key"] == "WM-9"
    assert result["url"] == f"{_BASE}/browse/WM-9"
    assert result["created"] == "2026-07-15T10:00:00.000+0200"
    fields = seen["payload"]["fields"]
    assert fields["project"] == {"key": "WM"}
    assert fields["summary"] == "Tytuł" and fields["labels"] == ["pilne"]
    assert isinstance(fields["description"], dict) and fields["description"]["type"] == "doc"
    assert adf_to_text(fields["description"]) == "Opis"  # zakodowane jako ADF


def test_add_comment_encodes_body_as_adf():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/issue/WM-5/comment"
        seen["payload"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "5001", "created": "2026-07-15T11:00:00.000+0200"})

    result = _client(handler).add_comment("WM-5", "treść komentarza")
    assert result["id"] == "5001"
    assert result["url"] == f"{_BASE}/browse/WM-5?focusedCommentId=5001"
    assert isinstance(seen["payload"]["body"], dict)
    assert adf_to_text(seen["payload"]["body"]) == "treść komentarza"


def test_create_issue_translates_http_error_to_write_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"errors": {"summary": "wymagane"}})

    with pytest.raises(WriteError, match="utworzyć zgłoszenia"):
        _client(handler).create_issue("WM", "Task", "", "opis")


# --- transition (ADR 0032) — v3 paths ---------------------------------------


def test_read_transitions_parses_current_status_and_neighbors():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/issue/WM-5"
        assert request.url.params.get("expand") == "transitions"
        return httpx.Response(
            200,
            json={
                "fields": {"status": {"name": "To Do"}},
                "transitions": [
                    {"id": "11", "name": "Start Progress", "to": {"name": "In Progress"}}
                ],
            },
        )

    snap = _client(handler).read_transitions("WM-5")
    assert snap["current_status"] == "To Do"
    assert snap["transitions"] == [
        {"id": "11", "name": "Start Progress", "to_status": "In Progress"}
    ]


def test_transition_issue_posts_id_and_fetches_status_updated():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.url.path == "/rest/api/3/issue/WM-5/transitions"
            seen["payload"] = json.loads(request.content)
            return httpx.Response(204)
        return httpx.Response(
            200,
            json={
                "fields": {
                    "status": {"name": "In Progress"},
                    "updated": "2026-07-15T10:00:00.000+0200",
                }
            },
        )

    result = _client(handler).transition_issue("WM-5", "11")
    assert seen["payload"] == {"transition": {"id": "11"}}
    assert result == {
        "url": f"{_BASE}/browse/WM-5",
        "status": "In Progress",
        "updated": "2026-07-15T10:00:00.000+0200",
    }


def test_transition_issue_survives_failed_followup_get():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(204)
        return httpx.Response(500)

    result = _client(handler).transition_issue("WM-5", "11")
    assert result["status"] == "" and result["updated"] == ""
    assert result["url"] == f"{_BASE}/browse/WM-5"


# --- widoczność cichych strat ------------------------------------------------------


def test_search_warns_when_inline_comments_were_truncated(caplog):
    """Cap 20/20 w bulk-searchu ma być SŁYSZALNY — inaczej zdarzenia przepadają po cichu.

    Zgłoszenie wygląda na kompletne, a część komentarzy nigdy nie trafi do Teams. Założenie
    ADR 0033 („poller inkrementalny → wystarcza") trzyma się tylko wtedy, gdy Jira zwraca
    20 NAJNOWSZYCH pozycji, a kolejność nie jest udokumentowana ani zmierzona.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "issues": [
                    {
                        "key": "WT-12",
                        "fields": {"comment": {"comments": [{"id": "1"}], "total": 25}},
                    }
                ],
                "isLast": True,
            },
        )

    _client(handler).search_issues("project = WT")

    assert "WT-12" in caplog.text
    assert "1 z 25" in caplog.text


def test_search_is_quiet_when_nothing_was_truncated(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "issues": [
                    {"key": "WT-1", "fields": {"comment": {"comments": [{"id": "1"}], "total": 1}}}
                ],
                "isLast": True,
            },
        )

    _client(handler).search_issues("project = WT")

    assert "przepadn" not in caplog.text and "NIE trafi" not in caplog.text


def test_myself_warns_when_account_timezone_differs_from_host(caplog):
    """JQL bez strefy liczy daty w strefie KONTA — rozjazd przesuwa granicę okna pollingu."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"accountId": "acc-1", "timeZone": "Pacific/Kiritimati"})

    _client(handler).authenticated_account()

    assert "Pacific/Kiritimati" in caplog.text
