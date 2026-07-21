"""Test integracji: prawdziwy ``HttpxJiraCloudClient`` (MockTransport) → ``JiraPoller`` → store.

Testy w ``test_jira_cloud_api.py`` sprawdzają klient Cloud w IZOLACJI (spłaszczanie ADF, paginacja),
a ``test_poller.py``/``test_selection.py`` — poller/selekcję na atrapie oddającej JUŻ płaski kształt
Server/DC. Ten plik domyka SZEW między nimi: realna odpowiedź Jira Cloud (opis/komentarz jako ADF,
autorzy jako ``accountId``, ``changelog`` inline z ``/search/jql``) przechodzi przez PRAWDZIWY
``search_issues`` (spłaszczenie ADF + przepuszczenie changelogu), a potem przez ``selection`` do
``NewEvent``. To łapie regresje niewidoczne w testach izolowanych: gdyby spłaszczony opis nie
trafiał do ``map_issue_created``, gdyby kształt changelogu Cloud rozjechał się z ``map_transitions``
albo gdyby self-skip po ``accountId`` przestał działać end-to-end.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from workmate.adapters.inbound.jira.poller import JiraPoller
from workmate.adapters.outbound.jira_cloud_api import HttpxJiraCloudClient
from workmate.core.application.events import EventService
from workmate.core.domain.adf import text_to_adf
from workmate.core.domain.events import Event, NewEvent

_BASE = "https://acme.atlassian.net"
_BOT_ACCOUNT = "acc-bot"


class _FakeStore:
    """Atrapa ``EventStore`` w pamięci z dedupem po kluczu (jak realny SQLite)."""

    def __init__(self) -> None:
        self.rows: list[Event] = []

    def exists(self, source, external_id, kind):
        return any(
            r.source == source and r.external_id == external_id and r.kind == kind
            for r in self.rows
        )

    def append(self, event: NewEvent) -> Event:
        existing = next(
            (
                r
                for r in self.rows
                if r.source == event.source
                and r.external_id == event.external_id
                and r.kind == event.kind
            ),
            None,
        )
        if existing is not None:
            return existing
        row = Event(id=len(self.rows) + 1, ingested_at=event.occurred_at, **event.model_dump())
        self.rows.append(row)
        return row

    def read_since(self, after_id, *, source=None, limit=50):
        return [r for r in self.rows if r.id > after_id]

    def recent(self, *, source=None, limit=20):
        return list(reversed(self.rows))


def _cloud_issue(
    key: str = "WM-1",
    *,
    creator_account: str = "acc-alice",
    description: str = "Opis zgłoszenia w ADF",
    with_transition: bool = True,
    with_comment: bool = True,
) -> dict:
    """Kształt issue z Jira Cloud: opis/komentarz jako ADF, autor jako ``accountId``."""
    fields: dict = {
        "summary": f"Zgłoszenie {key}",
        "description": text_to_adf(description),  # ADF (jak zwraca Cloud), nie goły string
        "created": "2026-07-15T10:00:00.000+0000",
        "updated": "2026-07-15T12:00:00.000+0000",
        "creator": {"accountId": creator_account, "displayName": "Alice"},
        "project": {"key": key.split("-", 1)[0]},
    }
    if with_comment:
        fields["comment"] = {
            "comments": [
                {
                    "id": "5001",
                    "author": {"accountId": "acc-carol"},
                    "body": text_to_adf("Komentarz w ADF\nz nową linią"),
                    "created": "2026-07-15T11:30:00.000+0000",
                }
            ]
        }
    raw: dict = {"key": key, "fields": fields}
    if with_transition:
        raw["changelog"] = {
            "histories": [
                {
                    "id": "h1",
                    "author": {"accountId": "acc-bob"},
                    "created": "2026-07-15T11:00:00.000+0000",
                    "items": [
                        {"field": "status", "fromString": "To Do", "toString": "In Progress"}
                    ],
                }
            ]
        }
    return raw


def _poller_over_cloud(issues: list[dict], store: _FakeStore) -> JiraPoller:
    """Poller na PRAWDZIWYM kliencie Cloud (MockTransport oddaje ``issues`` z /search/jql)."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/search/jql"
        body = json.loads(request.content)
        assert body["expand"] == "changelog"  # poller musi zażądać changelogu (tranzycje)
        return httpx.Response(200, json={"isLast": True, "issues": issues})

    client = HttpxJiraCloudClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
        email="bot@example.com",
        token="api-token",
        base_url=_BASE,
    )
    return JiraPoller(
        client,
        EventService(store),
        base_url=_BASE,
        watch_projects=("WM",),
        state={},
        persist=lambda s: None,
        poll_interval=1,
        per_page=50,
        self_account=_BOT_ACCOUNT,
        project_map={"WM": "workmate"},
    )


def test_cloud_issue_flows_through_poller_with_flattened_adf():
    store = _FakeStore()
    ingested = asyncio.run(_poller_over_cloud([_cloud_issue("WM-1")], store).poll_once())

    assert ingested == 3  # utworzenie + tranzycja + komentarz z jednego issue Cloud
    by_kind = {r.kind: r for r in store.rows}
    assert set(by_kind) == {"jira_issue_created", "jira_transition", "jira_comment"}
    # ADF opisu spłaszczony do tekstu jeszcze w kliencie → selection dostaje string, nie dict.
    assert by_kind["jira_issue_created"].summary == "Opis zgłoszenia w ADF"
    # ADF komentarza (z twardą nową linią) spłaszczony i przeniesiony wiernie.
    assert by_kind["jira_comment"].summary == "Komentarz w ADF\nz nową linią"
    assert by_kind["jira_transition"].summary == "To Do → In Progress"
    # Atrybucja projektu z mapy rejestru (ADR 0028) po spłaszczeniu.
    assert by_kind["jira_issue_created"].project == "workmate"


def test_cloud_self_skip_by_account_id_end_to_end():
    store = _FakeStore()
    # Jedno issue autorstwa konta bota (accountId), drugie od człowieka. Self-skip po accountId.
    issues = [
        _cloud_issue(
            "WM-1", creator_account=_BOT_ACCOUNT, with_transition=False, with_comment=False
        ),
        _cloud_issue(
            "WM-2", creator_account="acc-alice", with_transition=False, with_comment=False
        ),
    ]
    ingested = asyncio.run(_poller_over_cloud(issues, store).poll_once())

    assert ingested == 1  # utworzenie autorstwa bota pominięte (strażnik pętli po accountId)
    assert {r.external_id for r in store.rows} == {"WM-2"}


def test_cloud_issue_without_description_maps_created_with_empty_summary():
    store = _FakeStore()
    issue = _cloud_issue("WM-1", with_transition=False, with_comment=False)
    issue["fields"]["description"] = (
        None  # brak opisu (Cloud zwraca None) — zostaje None po flatten
    )

    ingested = asyncio.run(_poller_over_cloud([issue], store).poll_once())

    assert ingested == 1
    created = store.rows[0]
    assert created.kind == "jira_issue_created"
    assert created.summary == ""  # None opisu → puste podsumowanie (jak Server/DC)
