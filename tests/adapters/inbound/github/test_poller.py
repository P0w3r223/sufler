"""Testy pętli pollera GitHub (poll_once) na atrapach — ingest, self-skip, watermark, dedup."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from workmate.adapters.inbound.github.poller import GithubPoller
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
    """Atrapa portu ``GithubReadPort`` (sync) — oddaje zaskryptowane listy i notuje ``since``."""

    def __init__(self, *, issues=(), comments=(), login="bot"):
        self._issues = list(issues)
        self._comments = list(comments)
        self._login = login
        self.since_seen: list = []

    def authenticated_login(self) -> str:
        return self._login

    def list_issues(self, owner, repo, *, since=None, per_page=50):
        self.since_seen.append(("issues", since))
        return self._issues

    def list_issue_comments(self, owner, repo, *, since=None, per_page=50):
        self.since_seen.append(("comments", since))
        return self._comments


def _issue(number: int, *, login: str = "alice", updated: str = "2026-07-15T10:00:00Z") -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": "opis",
        "html_url": f"http://gh/{number}",
        "user": {"login": login},
        "created_at": "2026-07-15T10:00:00Z",
        "updated_at": updated,
    }


def _poller(client, store, *, state=None, self_login="bot"):
    return GithubPoller(
        client,
        EventService(store),
        owner="o",
        repo="r",
        watch_kinds=("issues", "comments"),
        state=state if state is not None else {},
        persist=lambda s: None,
        poll_interval=1,
        per_page=50,
        self_login=self_login,
    )


def test_poll_once_ingests_new_events():
    store = _FakeStore()
    client = _FakeClient(issues=[_issue(1), _issue(2)])
    ingested = asyncio.run(_poller(client, store).poll_once())
    assert ingested == 2
    assert {r.external_id for r in store.rows} == {"1", "2"}


def test_poll_once_skips_self_authored():
    store = _FakeStore()
    client = _FakeClient(issues=[_issue(1, login="bot"), _issue(2, login="alice")])
    ingested = asyncio.run(_poller(client, store, self_login="bot").poll_once())
    assert ingested == 1  # issue autorstwa "bot" (konto PAT) pominięte
    assert {r.external_id for r in store.rows} == {"2"}


def test_poll_once_dedups_across_rounds():
    store = _FakeStore()
    client = _FakeClient(issues=[_issue(1), _issue(2)])
    poller = _poller(client, store)
    assert asyncio.run(poller.poll_once()) == 2
    assert asyncio.run(poller.poll_once()) == 0  # te same issue → dedup magazynu, 0 nowych
    assert len(store.rows) == 2


def test_poll_once_advances_watermark_after_ingest():
    state: dict = {}
    client = _FakeClient(issues=[_issue(1, updated="2026-07-15T10:00:00Z"),
                                 _issue(2, updated="2026-07-15T12:00:00Z")])
    asyncio.run(_poller(client, _FakeStore(), state=state).poll_once())
    assert state["issues_since"] == "2026-07-15T12:00:00Z"


def test_poll_once_isolates_poisoned_event():
    """Jedno zdarzenie ze znakiem sterującym NIE wywraca rundy — pomijamy je, watermark rusza."""
    store = _FakeStore()
    poisoned = _issue(1)
    poisoned["title"] = "zła\x00treść"  # znak sterujący z niezaufanej treści GitHuba
    client = _FakeClient(issues=[poisoned, _issue(2)])
    state: dict = {}
    ingested = asyncio.run(_poller(client, store, state=state).poll_once())
    assert ingested == 1  # zatrute pominięte, czyste (2) przyjęte
    assert {r.external_id for r in store.rows} == {"2"}
    assert state["issues_since"]  # watermark PRZESUNIĘTY mimo zatrutego → brak zakleszczenia


def test_resolve_self_login_from_client_when_unset():
    poller = _poller(_FakeClient(login="octocat"), _FakeStore(), self_login="")
    asyncio.run(poller._resolve_self_login())
    assert poller._self_login == "octocat"
