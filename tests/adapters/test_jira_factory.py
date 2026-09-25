"""Testy fabryki build_jira_client (ADR 0033) — wybór implementacji wg deployment."""

from __future__ import annotations

import httpx

from sufler.adapters.outbound.jira_api import HttpxJiraClient, build_jira_client
from sufler.adapters.outbound.jira_cloud_api import HttpxJiraCloudClient
from sufler.config import JiraSettings


def _settings(**kw) -> JiraSettings:
    base: dict = {
        "base_url": "https://acme.example",
        "token": "secret",
    }
    base.update(kw)
    return JiraSettings(**base)


def test_factory_default_builds_server_client():
    client = build_jira_client(httpx.Client(), _settings())
    assert isinstance(client, HttpxJiraClient)


def test_factory_server_deployment_builds_server_client():
    client = build_jira_client(httpx.Client(), _settings(deployment="server"))
    assert isinstance(client, HttpxJiraClient)


def test_factory_cloud_deployment_builds_cloud_client():
    client = build_jira_client(
        httpx.Client(), _settings(deployment="cloud", email="me@example.com")
    )
    assert isinstance(client, HttpxJiraCloudClient)


def test_factory_cloud_is_case_insensitive():
    client = build_jira_client(
        httpx.Client(), _settings(deployment="Cloud", email="me@example.com")
    )
    assert isinstance(client, HttpxJiraCloudClient)


def test_factory_tolerates_surrounding_whitespace_in_deployment():
    """Wartość idzie z ``.env`` — spacja albo CR po ``cloud`` (plik z Windows) nie może cicho
    zrzucić wyboru na Server/DC, bo klient Server/DC uderzy w REST v2, którego Cloud nie ma."""
    client = build_jira_client(
        httpx.Client(), _settings(deployment=" cloud\t", email="me@example.com")
    )
    assert isinstance(client, HttpxJiraCloudClient)


def test_factory_falls_back_to_server_on_an_unknown_deployment_value():
    """Literówka w ``SUFLER_JIRA_DEPLOYMENT`` daje Server/DC — świadomy fallback, nie wyjątek
    przy starcie. Test pinuje KTÓRA to gałąź: „nieznane → cloud" byłoby wysłaniem PAT-u Basic-iem.
    """
    client = build_jira_client(httpx.Client(), _settings(deployment="clod"))
    assert isinstance(client, HttpxJiraClient)


def test_cloud_client_receives_the_credentials_from_settings():
    """Fabryka jest JEDYNYM miejscem, gdzie e-mail spotyka token — przestawienie argumentów
    (``email=token``) daje klienta, który buduje się bez błędu i dostaje 401 dopiero na żywej
    Jirze. Sprawdzamy przez nagłówek, bo to jedyne obserwowalne wyjście tej decyzji.
    """
    import base64

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"accountId": "acc-1"})

    transport = httpx.Client(transport=httpx.MockTransport(handler))
    client = build_jira_client(
        transport,
        _settings(deployment="cloud", email="me@example.com", token="api-token"),
    )
    client.authenticated_account()

    assert base64.b64decode(seen["auth"].split(" ", 1)[1]).decode() == "me@example.com:api-token"


def test_server_client_sends_the_token_as_a_bearer_pat():
    """Druga strona tej samej decyzji: Server/DC bierze PAT Bearerem, nie Basic-iem."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"name": "svc-bot"})

    transport = httpx.Client(transport=httpx.MockTransport(handler))
    build_jira_client(transport, _settings(token="pat-secret")).authenticated_account()

    assert seen["auth"] == "Bearer pat-secret"
