"""Testy klienta Jira REST v2 (HttpxJiraClient) na ``httpx.MockTransport`` — bez sieci.

Sedno: nagłówek PAT Bearer, odczyt konta (/myself), paginacja /search po ``startAt`` (koperta
``{issues, total, ...}``) z twardym sufitem stron, szczegóły zgłoszenia i komentarze oraz
WALIDACJA klucza przed wstawieniem go do ścieżki URL. Zapis/tranzycja (ADR 0031/0032) zostały
USUNIĘTE razem z mostem (ADR 0054) — klient jest dziś wyłącznie odczytowy.
"""

from __future__ import annotations

import httpx
import pytest

from sufler.adapters.outbound.jira_api import HttpxJiraClient
from sufler.core.errors import InvalidRequestError, JiraReadError

_BASE = "https://jira.example.com"


def _client(handler) -> HttpxJiraClient:
    transport = httpx.MockTransport(handler)
    return HttpxJiraClient(httpx.Client(transport=transport), "PAT-secret", base_url=_BASE)


def test_sets_bearer_auth_and_reads_account():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"name": "svc-bot"})

    account = _client(handler).authenticated_account()
    assert account == "svc-bot"
    assert seen["auth"] == "Bearer PAT-secret"
    assert seen["path"] == "/rest/api/2/myself"


def test_search_issues_paginates_by_start_at():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        start = int(request.url.params.get("startAt"))
        key = "WM-1" if start == 0 else "WM-2"
        return httpx.Response(
            200, json={"issues": [{"key": key}], "total": 2, "startAt": start, "maxResults": 1}
        )

    issues = _client(handler).search_issues("project=WM", max_results=1)
    assert [i["key"] for i in issues] == ["WM-1", "WM-2"]
    assert calls["n"] == 2


def test_search_issues_sends_jql_and_expand():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["jql"] = request.url.params.get("jql")
        seen["expand"] = request.url.params.get("expand")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"issues": [], "total": 0})

    _client(handler).search_issues("project=WM AND updated>='2026-07-01'")
    assert seen["path"] == "/rest/api/2/search"
    assert "project=WM" in seen["jql"]
    assert seen["expand"] == "changelog"


def test_search_stops_at_the_page_ceiling():
    """Serwer, który zawyża ``total`` (albo w kółko oddaje pełne strony), zapętliłby pobieranie
    na godziny i zjadł limit zapytań. Klient Cloud ma tę obronę przetestowaną — Server/DC nie
    miał jej wcale, choć ``_MAX_PAGES`` stoi w obu.
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200, json={"issues": [{"key": f"WM-{calls['n']}"}], "total": 10_000, "maxResults": 1}
        )

    issues = _client(handler).search_issues("project=WM", max_results=1)

    assert calls["n"] == 10  # twardy cap stron, nie 10 000 / 1
    assert len(issues) == 10


def test_search_stops_on_an_empty_page_even_when_total_promises_more():
    """``total`` bywa nieaktualne (indeks Jiry jest asynchroniczny). Pusta strona przy dodatnim
    ``total`` musi kończyć pętlę, inaczej dobijamy do sufitu na pustych zapytaniach."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"issues": [], "total": 500})

    assert _client(handler).search_issues("project=WM") == []
    assert calls["n"] == 1


def test_search_survives_a_response_that_is_not_an_envelope():
    """Proxy/portal logowania potrafi oddać 200 z listą albo napisem zamiast koperty — to ma być
    pusty wynik, nie ``AttributeError`` w środku tury agenta."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["nie", "koperta"])

    assert _client(handler).search_issues("project=WM") == []


# --- konto: łańcuch odwzorowań name → key → accountId ---------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"name": "svc-bot", "key": "k", "accountId": "a"}, "svc-bot"),
        ({"key": "svc-key", "accountId": "a"}, "svc-key"),
        ({"accountId": "acc-1"}, "acc-1"),
        ({"displayName": "Bot"}, ""),
        (["lista"], ""),
    ],
    ids=["name", "key", "accountId", "brak pól", "nie-słownik"],
)
def test_authenticated_account_falls_back_through_the_id_fields(payload, expected):
    """Server/DC oddaje ``name``, DC za SSO bywa że tylko ``key``, a hybryda ``accountId``.
    Konto zasila JQL ``assignee = ...``; pusty napis to czytelna odmowa, wyjątek — wywrócona tura.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    assert _client(handler).authenticated_account() == expected


# --- klucz zgłoszenia w ŚCIEŻCE URL: walidacja przed wysłaniem ------------------


@pytest.mark.parametrize(
    "key",
    ["../../../rest/api/2/myself", "WM-1/../../admin", "WM-1?expand=all", "", "WM", "1-WM", "WM-x"],
    ids=[
        "traversal",
        "traversal w kluczu",
        "doklejony parametr",
        "pusty",
        "bez numeru",
        "cyfra na start",
        "numer nie-cyfra",
    ],
)
def test_issue_key_is_validated_before_it_reaches_the_url(key: str):
    """Klucz przychodzi OD MODELU i jest wklejany do ŚCIEŻKI, nie do parametru — bez walidacji
    ``../`` wyprowadza zapytanie na dowolny endpoint tego samego serwera, z naszym PAT-em.

    Odmowa musi paść PRZED wysłaniem: sam fakt zapytania jest już wyciekiem intencji.
    """
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    client = _client(handler)
    with pytest.raises(InvalidRequestError, match="Niepoprawny klucz"):
        client.get_issue(key)
    with pytest.raises(InvalidRequestError, match="Niepoprawny klucz"):
        client.list_comments(key)

    assert calls == []  # ani jednego żądania


def test_issue_key_with_surrounding_whitespace_is_accepted_trimmed():
    """Model kleja klucz z prozy — spacja wokół to pomyłka, nie atak."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"key": "WM-5"})

    assert _client(handler).get_issue("  WM-5 ")["key"] == "WM-5"
    assert seen["path"] == "/rest/api/2/issue/WM-5"


# --- szczegóły zgłoszenia i komentarze ------------------------------------------


def test_get_issue_asks_for_the_detail_fields():
    """Bez ``fields`` Jira oddaje KOMPLET pól (dziesiątki kilobajtów na zgłoszenie) — to jedzie
    prosto do kontekstu modelu."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["fields"] = request.url.params.get("fields")
        return httpx.Response(200, json={"key": "WM-5", "fields": {"summary": "Naprawa"}})

    issue = _client(handler).get_issue("WM-5")

    assert issue["fields"]["summary"] == "Naprawa"
    for field in ("summary", "status", "priority", "assignee"):
        assert field in seen["fields"]


def test_get_issue_returns_empty_dict_when_the_body_is_not_an_object():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["nie", "obiekt"])

    assert _client(handler).get_issue("WM-5") == {}


def test_list_comments_returns_the_newest_tail_not_the_head():
    """Server/DC oddaje komentarze ROSNĄCO i ignoruje ``orderBy`` — bez ucięcia ogona
    „ostatnie komentarze" pokazywałyby te NAJSTARSZE, czyli dokładną odwrotność obietnicy."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"comments": [{"id": str(i)} for i in range(1, 8)]},  # 1..7 rosnąco
        )

    comments = _client(handler).list_comments("WM-5", max_results=3)

    assert [c["id"] for c in comments] == ["5", "6", "7"]


@pytest.mark.parametrize(
    "body",
    [{}, {"comments": None}, {"comments": ["napis", None, 7]}],
    ids=["brak pola", "null zamiast listy", "śmieci w liście"],
)
def test_list_comments_tolerates_a_missing_or_malformed_comments_field(body):
    """Zgłoszenie bez komentarzy i nie-słownikowe elementy dają pustą listę, nie wyjątek —
    ta odpowiedź ląduje w turze agenta, więc awaria kształtu nie może jej wywrócić."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    assert _client(handler).list_comments("WM-5") == []


def test_page_ceiling_leaves_a_warning_with_the_resource_name(caplog):
    """Ucięcie na suficie stron było CICHE — brakujące zgłoszenia wyglądały jak „tyle było".

    Sufit chroni przed nieograniczoną paginacją i ma zostać, ale wynik wraca wtedy NIEPEŁNY,
    a jedyną informacją o tym jest ostrzeżenie w logu (por. ``transcript_sources``, które w tym
    miejscu podnosi twardy błąd).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("startAt"))
        return httpx.Response(
            200,
            json={
                "issues": [{"key": f"WM-{start}"}],
                "total": 10_000,
                "startAt": start,
                "maxResults": 1,
            },
        )

    with caplog.at_level("WARNING"):
        issues = _client(handler).search_issues("project=WM", max_results=1)

    assert len(issues) == 10  # sufit stron
    assert any("rest/api/2/search" in rec.getMessage() for rec in caplog.records)


def test_auth_error_is_translated_to_a_domain_error_at_the_adapter_boundary():
    """Słownik sieci kończy się na adapterze — rdzeń ma dostać ``JiraReadError``, nie ``httpx``.

    Tłumaczenie stało dotąd w warstwie aplikacji i wciągało ``import httpx`` do heksagonu.
    ``lint-imports`` tego nie widzi (reguła zabrania tylko importów z ``sufler.adapters``), więc
    jedyną bramką jest ta sonda.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errorMessages": ["unauthorized"]})

    with pytest.raises(JiraReadError, match="brak dostępu"):
        _client(handler).search_issues("project=WM")


def test_timeout_is_translated_to_a_domain_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("cisza", request=request)

    with pytest.raises(JiraReadError, match="timeout"):
        _client(handler).get_issue("WM-1")


def test_network_failure_is_translated_to_a_domain_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("brak trasy", request=request)

    with pytest.raises(JiraReadError, match="połączyć się z Jirą|połączyć z Jirą"):
        _client(handler).authenticated_account()
