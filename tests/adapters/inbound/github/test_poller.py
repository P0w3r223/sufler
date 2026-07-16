"""Testy pętli pollera GitHub (poll_once) na atrapach — ingest, self-skip, watermark, dedup."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from workmate.adapters.inbound.github.poller import GithubPoller, _iso_z
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

    def __init__(self, *, issues=(), comments=(), runs=(), reviews_by_pr=None, login="bot"):
        self._issues = list(issues)
        self._comments = list(comments)
        self._runs = list(runs)
        self._reviews_by_pr = dict(reviews_by_pr or {})
        self._login = login
        self.since_seen: list = []
        self.reviews_seen: list = []  # numery PR odpytane o recenzje

    def authenticated_login(self) -> str:
        return self._login

    def list_issues(self, owner, repo, *, since=None, per_page=50):
        self.since_seen.append(("issues", since))
        return self._issues

    def list_issue_comments(self, owner, repo, *, since=None, per_page=50):
        self.since_seen.append(("comments", since))
        return self._comments

    def list_workflow_runs(self, owner, repo, *, per_page=50, status="completed"):
        self.since_seen.append(("runs", None))
        return self._runs

    def list_pull_reviews(self, owner, repo, pull_number):
        self.reviews_seen.append(pull_number)
        return self._reviews_by_pr.get(pull_number, [])


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


def _pr(number: int, *, state: str = "open", login: str = "alice") -> dict:
    raw = _issue(number, login=login)
    raw["pull_request"] = {"url": f"http://api/pr/{number}"}
    raw["state"] = state
    return raw


def _run(run_id: int, *, conclusion: str = "success", attempt: int = 1,
         updated: str = "2026-07-15T13:00:00Z") -> dict:
    return {
        "id": run_id,
        "name": "CI",
        "conclusion": conclusion,
        "run_attempt": attempt,
        "html_url": f"https://github.com/o/r/actions/runs/{run_id}",
        "pull_requests": [],
        "repository": {"html_url": "https://github.com/o/r"},
        "updated_at": updated,
    }


def _review(review_id: int, *, state: str = "APPROVED", login: str = "alice", pr: int = 12,
            submitted: str = "2026-07-15T12:00:00Z") -> dict:
    return {
        "id": review_id,
        "state": state,
        "body": "recenzja",
        "html_url": f"https://github.com/o/r/pull/{pr}#pullrequestreview-{review_id}",
        "user": {"login": login},
        "submitted_at": submitted,
    }


def _poller(client, store, *, state=None, self_login="bot", watch=("issues", "comments")):
    return GithubPoller(
        client,
        EventService(store),
        owner="o",
        repo="r",
        watch_kinds=watch,
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


def test_poll_once_ingests_ci_runs():
    store = _FakeStore()
    client = _FakeClient(runs=[_run(1, conclusion="success"), _run(2, conclusion="failure")])
    ingested = asyncio.run(_poller(client, store, watch=("ci",)).poll_once())
    assert ingested == 2
    assert {(r.kind, r.external_id) for r in store.rows} == {
        ("ci_success", "1#1"), ("ci_failure", "2#1"),
    }
    assert ("runs", None) in client.since_seen  # gałąź CI odpytała klienta


def test_poll_once_advances_runs_watermark():
    state: dict = {}
    client = _FakeClient(runs=[_run(1, updated="2026-07-15T13:00:00Z"),
                               _run(2, updated="2026-07-15T18:00:00Z")])
    asyncio.run(_poller(client, _FakeStore(), state=state, watch=("ci",)).poll_once())
    assert state["runs_since"] == "2026-07-15T18:00:00Z"


def test_fetch_reviews_only_queries_open_pull_requests():
    store = _FakeStore()
    client = _FakeClient(
        issues=[_pr(10, state="open"), _pr(11, state="closed"), _issue(12)],
        reviews_by_pr={10: [_review(1, login="alice")]},
    )
    ingested = asyncio.run(
        _poller(client, store, watch=("issues", "reviews")).poll_once()
    )
    # Tylko otwarty PR #10 odpytany o recenzje (zamknięty PR i zwykłe issue pominięte).
    assert client.reviews_seen == [10]
    assert ingested >= 1
    assert any(r.kind == "pr_review" for r in store.rows)


def test_fetch_reviews_respects_cap():
    # Więcej otwartych PR niż cap — odpytujemy najwyżej _MAX_REVIEW_PRS.
    from workmate.adapters.inbound.github.poller import _MAX_REVIEW_PRS

    prs = [_pr(n, state="open") for n in range(1, _MAX_REVIEW_PRS + 6)]
    client = _FakeClient(issues=prs)
    asyncio.run(_poller(client, _FakeStore(), watch=("issues", "reviews")).poll_once())
    assert len(client.reviews_seen) == _MAX_REVIEW_PRS


def test_poll_once_advances_reviews_watermark_by_submitted_at():
    state: dict = {}
    client = _FakeClient(
        issues=[_pr(10, state="open")],
        reviews_by_pr={10: [_review(1, submitted="2026-07-15T12:00:00Z"),
                            _review(2, submitted="2026-07-15T16:00:00Z")]},
    )
    asyncio.run(
        _poller(client, _FakeStore(), state=state, watch=("issues", "reviews")).poll_once()
    )
    assert state["reviews_since"] == "2026-07-15T16:00:00Z"


def test_seed_initializes_all_four_watermarks():
    poller = _poller(_FakeClient(), _FakeStore(), watch=("issues", "comments", "pulls",
                                                         "reviews", "ci"))
    poller._seed("2026-07-15T00:00:00Z")
    state = poller._state
    assert set(state) == {"issues_since", "comments_since", "runs_since", "reviews_since"}
    assert all(v == "2026-07-15T00:00:00Z" for v in state.values())


def test_iso_z_seeds_in_github_format_not_isoformat():
    """Seed watermarku musi mieć format GitHuba (``…Z``), nie ``isoformat`` (``+00:00``) — inaczej
    porównanie leksykograficzne watermarków CI/recenzji ze znacznikami GitHuba by się rozjechało."""
    seeded = _iso_z(datetime(2026, 7, 16, 10, 38, 26, 123456, tzinfo=timezone.utc))
    assert seeded == "2026-07-16T10:38:26Z"  # bez ułamków sekund i bez „+00:00"
