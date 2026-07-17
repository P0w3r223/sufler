"""Testy pętli pollera Jiry (poll_once) na atrapach — ingest, self-skip, watermark, dedup, JQL."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from workmate.adapters.inbound.jira.poller import JiraPoller
from workmate.core.application.events import EventService
from workmate.core.domain.events import Event, NewEvent

_WHEN = datetime(2026, 7, 15, tzinfo=timezone.utc)


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
        row = Event(id=len(self.rows) + 1, ingested_at=_WHEN, **event.model_dump())
        self.rows.append(row)
        return row

    def read_since(self, after_id, *, source=None, limit=50):
        return [r for r in self.rows if r.id > after_id]

    def recent(self, *, source=None, limit=20):
        return list(reversed(self.rows))


class _FakeClient:
    """Atrapa portu ``JiraReadPort`` (sync) — oddaje zaskryptowane issue i notuje JQL."""

    def __init__(self, *, issues=(), account="svc-bot"):
        self._issues = list(issues)
        self._account = account
        self.jql_seen: list[str] = []

    def authenticated_account(self) -> str:
        return self._account

    def search_issues(self, jql, *, max_results=50, expand="changelog"):
        self.jql_seen.append(jql)
        return self._issues


def _issue(
    key: str = "WM-1",
    *,
    creator: str = "alice",
    updated: str = "2026-07-15T10:00:00.000+0200",
    histories: list | None = None,
    comments: list | None = None,
) -> dict:
    fields: dict = {
        "summary": f"Zgłoszenie {key}",
        "description": "opis",
        "created": "2026-07-15T10:00:00.000+0200",
        "updated": updated,
        "creator": {"name": creator},
        "project": {"key": key.split("-", 1)[0]},
    }
    if comments is not None:
        fields["comment"] = {"comments": comments}
    raw: dict = {"key": key, "fields": fields}
    if histories is not None:
        raw["changelog"] = {"histories": histories}
    return raw


def _poller(client, store, *, state=None, self_account="svc-bot", watch=("WM",), project_map=None):
    return JiraPoller(
        client,
        EventService(store),
        base_url="https://jira.example.com",
        watch_projects=watch,
        state=state if state is not None else {},
        persist=lambda s: None,
        poll_interval=1,
        per_page=50,
        self_account=self_account,
        project_map=project_map,
    )


def test_poll_once_ingests_created_transition_comment():
    store = _FakeStore()
    raw = _issue(
        "WM-1",
        creator="alice",
        histories=[
            {
                "id": "h1",
                "author": {"name": "bob"},
                "created": "2026-07-15T11:00:00.000+0200",
                "items": [{"field": "status", "fromString": "Open", "toString": "Done"}],
            }
        ],
        comments=[
            {
                "id": "c1",
                "author": {"name": "carol"},
                "body": "gotowe",
                "created": "2026-07-15T12:00:00.000+0200",
            }
        ],
    )
    ingested = asyncio.run(_poller(_FakeClient(issues=[raw]), store).poll_once())
    assert ingested == 3
    assert {(r.kind, r.external_id) for r in store.rows} == {
        ("jira_issue_created", "WM-1"),
        ("jira_transition", "h1"),
        ("jira_comment", "c1"),
    }


def test_poll_once_skips_self_authored_issue():
    store = _FakeStore()
    client = _FakeClient(
        issues=[_issue("WM-1", creator="svc-bot"), _issue("WM-2", creator="alice")]
    )
    ingested = asyncio.run(_poller(client, store, self_account="svc-bot").poll_once())
    assert ingested == 1  # zgłoszenie autorstwa konta PAT pominięte
    assert {r.external_id for r in store.rows} == {"WM-2"}


def test_poll_once_dedups_across_rounds():
    store = _FakeStore()
    client = _FakeClient(issues=[_issue("WM-1"), _issue("WM-2")])
    poller = _poller(client, store)
    assert asyncio.run(poller.poll_once()) == 2
    assert asyncio.run(poller.poll_once()) == 0  # te same issue → dedup magazynu, 0 nowych
    assert len(store.rows) == 2


def test_poll_once_advances_watermark_after_ingest():
    state: dict = {}
    client = _FakeClient(
        issues=[
            _issue("WM-1", updated="2026-07-15T10:00:00.000+0200"),
            _issue("WM-2", updated="2026-07-15T12:30:00.000+0200"),
        ]
    )
    asyncio.run(_poller(client, _FakeStore(), state=state).poll_once())
    assert state["issues_since"] == "2026-07-15T12:30:00+02:00"


def test_poll_once_builds_jql_with_projects_and_watermark():
    client = _FakeClient(issues=[])
    state = {"issues_since": "2026-07-15T09:00:00+02:00"}  # ISO instant → JQL degraduje do minut
    asyncio.run(_poller(client, _FakeStore(), state=state, watch=("WM", "OPS")).poll_once())
    expected = 'project in (WM, OPS) AND updated >= "2026-07-15 09:00" ORDER BY updated ASC'
    assert client.jql_seen == [expected]


def test_poll_once_stamps_project_from_map():
    store = _FakeStore()
    client = _FakeClient(issues=[_issue("WM-1", creator="alice")])
    poller = _poller(client, store, project_map={"WM": "workmate"})
    asyncio.run(poller.poll_once())
    assert store.rows[0].project == "workmate"


def test_poll_once_isolates_poisoned_event():
    """Jedno zdarzenie ze znakiem sterującym NIE wywraca rundy — pomijamy je, watermark rusza."""
    store = _FakeStore()
    poisoned = _issue("WM-1")
    poisoned["fields"]["summary"] = "zła\x00treść"  # znak sterujący z niezaufanej treści Jiry
    client = _FakeClient(issues=[poisoned, _issue("WM-2")])
    state: dict = {}
    ingested = asyncio.run(_poller(client, store, state=state).poll_once())
    assert ingested == 1  # zatrute pominięte, czyste (WM-2) przyjęte
    assert {r.external_id for r in store.rows} == {"WM-2"}
    assert state["issues_since"]  # watermark PRZESUNIĘTY mimo zatrutego → brak zakleszczenia


def test_resolve_self_account_from_client_when_unset():
    poller = _poller(_FakeClient(account="octo-svc"), _FakeStore(), self_account="")
    asyncio.run(poller._resolve_self_account())
    assert poller._self_account == "octo-svc"


def test_seed_initializes_issues_since_once():
    poller = _poller(_FakeClient(), _FakeStore())
    poller._seed("2026-07-15 00:00")
    assert poller._state["issues_since"] == "2026-07-15 00:00"
    poller._seed("2026-07-16 00:00")  # idempotentny — nie nadpisuje
    assert poller._state["issues_since"] == "2026-07-15 00:00"
