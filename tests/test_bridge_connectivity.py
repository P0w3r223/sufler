"""Test ŁĄCZNOŚCI mostu Faza 3 — GitHub ↔ EventStore ↔ Teams wpięte RAZEM (integracja).

W odróżnieniu od testów jednostkowych (każdy element osobno), tu składamy REALNE komponenty:
prawdziwy ``SqliteEventStore`` (wspólny kręgosłup, plik na dysku), realny ``GithubPoller``,
``EventNotifier``, ``GithubWriteService`` i narzędzia agenta. Atrapujemy TYLKO dwie granice
sieciowe, których w testach nie dosięgamy: klienta GitHub REST i wysyłkę do Microsoft Graph.
Dowodzi, że warstwy naprawdę „się widzą" i przepływ działa od końca do końca oraz że oba
strażniki pętli trzymają.
"""

from __future__ import annotations

import asyncio

from workmate.adapters.inbound.github.poller import GithubPoller
from workmate.adapters.outbound.sqlite_events import SqliteEventStore
from workmate.core.application.events import EventService
from workmate.core.application.github import GithubWriteService
from workmate.core.application.notifier import EventNotifier, NotifyTargets
from workmate.core.application.tools import (
    build_events_catalog,
    build_github_write_catalog,
)

_SELF = "workmate-bot"


# --- Atrapy DWÓCH granic sieciowych (jedyne, czego tu nie dosięgamy) -----------------
class _FakeGithubReadClient:
    """Atrapa GitHub REST (read) — oddaje zaskryptowane issue/komentarze."""

    def __init__(self, *, issues=(), comments=()):
        self._issues = list(issues)
        self._comments = list(comments)

    def authenticated_login(self) -> str:
        return _SELF

    def list_issues(self, owner, repo, *, since=None, per_page=50):
        return self._issues

    def list_issue_comments(self, owner, repo, *, since=None, per_page=50):
        return self._comments


class _FakeGithubWriteClient:
    """Atrapa GitHub REST (write) — notuje wywołania i zwraca odpowiedź jak GitHub."""

    def __init__(self):
        self.created: list = []

    def create_issue(self, owner, repo, title, body, labels):
        self.created.append((owner, repo, title))
        return {
            "number": 101,
            "html_url": "https://github.com/biap/workmate/issues/101",
            "created_at": "2026-07-15T12:00:00Z",
        }

    def create_comment(self, owner, repo, issue_number, body):
        return {"id": 9, "html_url": "http://gh/c/9", "created_at": "2026-07-15T12:05:00Z"}


class _FakeTeamsSender:
    """Atrapa wysyłki do Teams (Graph) — notuje, co i gdzie poszło."""

    def __init__(self):
        self.chats: list = []
        self.channels: list = []

    async def send_chat(self, target_user_id, text):
        self.chats.append((target_user_id, text))

    async def post_channel(self, team_id, channel_id, text):
        self.channels.append((team_id, channel_id, text))


def _issue(number, *, login="alice", title=None):
    return {
        "number": number,
        "title": title or f"Issue {number}",
        "body": "szczegóły zgłoszenia",
        "html_url": f"https://github.com/biap/workmate/issues/{number}",
        "user": {"login": login},
        "created_at": "2026-07-15T10:00:00Z",
        "updated_at": "2026-07-15T10:00:00Z",
    }


def _poller(client, events, *, state=None):
    return GithubPoller(
        client,
        events,
        owner="biap",
        repo="workmate",
        watch_kinds=("issues", "comments"),
        state=state if state is not None else {},
        persist=lambda _s: None,
        poll_interval=1,
        per_page=50,
        self_login=_SELF,
    )


def _notifier(events, sender, *, cursor=0):
    targets = NotifyTargets(
        chat_user_id="user-9",
        team_id="team-1",
        channel_id="chan-1",
        enable_chat=True,
        enable_channel=True,
    )
    return EventNotifier(
        events, sender, targets=targets, save_cursor=lambda _c: None, cursor=cursor
    )


def _create_issue_tool(events):
    """Narzędzie create_github_issue nad realnym serwisem zapisu (atrapa klienta GitHub)."""
    service = GithubWriteService(
        _FakeGithubWriteClient(), owner="biap", repo="workmate", events=events
    )
    return next(
        s.fn for s in build_github_write_catalog(service) if s.name == "create_github_issue"
    )


# --- ŁĄCZNOŚĆ 1: GitHub → EventStore → Teams (oba cele), przez OSOBNE połączenia -------
def test_github_issue_flows_through_eventstore_to_both_teams_targets(tmp_path):
    db = tmp_path / "events.db"
    # DWA połączenia do jednego pliku = symulacja dwóch procesów (drzwi GitHub + notifier).
    writer_events = EventService(SqliteEventStore(db))
    reader_events = EventService(SqliteEventStore(db))
    sender = _FakeTeamsSender()

    client = _FakeGithubReadClient(issues=[_issue(7, title="Awaria API")])
    ingested = asyncio.run(_poller(client, writer_events).poll_once())
    pushed = asyncio.run(_notifier(reader_events, sender).pump_once())

    assert ingested == 1
    assert pushed == 1
    # Ten sam fakt dotarł do OBU celów Teams, przez wspólny EventStore (dwa połączenia).
    assert len(sender.chats) == 1 and len(sender.channels) == 1
    assert "Awaria API" in sender.chats[0][1]
    assert "Awaria API" in sender.channels[0][2]


# --- STRAŻNIK PĘTLI 1: własne zdarzenie (konto PAT) nie wchodzi ------------------------
def test_self_authored_github_event_is_skipped(tmp_path):
    events = EventService(SqliteEventStore(tmp_path / "events.db"))
    client = _FakeGithubReadClient(issues=[_issue(7, login=_SELF), _issue(8, login="alice")])
    ingested = asyncio.run(_poller(client, events).poll_once())
    assert ingested == 1  # issue autorstwa bota (PAT) pominięte, cudze przyjęte
    assert [e.external_id for e in events.recent()] == ["8"]


# --- ŁĄCZNOŚĆ 2: Teams → GitHub (zapis) → echo do EventStore ---------------------------
def test_teams_created_issue_echoes_to_eventstore(tmp_path):
    events = EventService(SqliteEventStore(tmp_path / "events.db"))
    writer = _FakeGithubWriteClient()
    service = GithubWriteService(writer, owner="biap", repo="workmate", events=events)
    catalog = build_github_write_catalog(service)
    create = next(s.fn for s in catalog if s.name == "create_github_issue")

    result = create(title="Prośba z Teams", body="treść")

    assert result == {
        "created": True,
        "number": 101,
        "url": "https://github.com/biap/workmate/issues/101",
    }
    assert writer.created == [("biap", "workmate", "Prośba z Teams")]  # repo z configu
    echoed = events.recent()
    assert len(echoed) == 1
    assert echoed[0].source == "teams" and echoed[0].kind == "github_issue_created"


# --- STRAŻNIK PĘTLI 2: echo Teams NIE wraca jako powiadomienie -------------------------
def test_teams_sourced_event_is_not_pushed_back_to_teams(tmp_path):
    db = tmp_path / "events.db"
    events = EventService(SqliteEventStore(db))
    create = _create_issue_tool(events)
    create(title="Prośba z Teams", body="treść")  # zapisuje zdarzenie source="teams"

    sender = _FakeTeamsSender()
    pushed = asyncio.run(_notifier(EventService(SqliteEventStore(db)), sender).pump_once())

    assert pushed == 0  # notifier wypycha tylko source="github" → echo Teams nie wraca
    assert sender.chats == [] and sender.channels == []


# --- „WIDZĄ SIĘ": read_recent_events pokazuje obie warstwy -----------------------------
def test_read_recent_events_sees_both_layers(tmp_path):
    db = tmp_path / "events.db"
    events = EventService(SqliteEventStore(db))
    # zdarzenie z GitHuba
    asyncio.run(_poller(_FakeGithubReadClient(issues=[_issue(7)]), events).poll_once())
    # zdarzenie z Teams (zapis do GitHub)
    _create_issue_tool(events)(title="Prośba z Teams", body="treść")

    # Narzędzie agenta (dostępne na dowolnych drzwiach) widzi OBIE warstwy.
    read = next(
        s.fn
        for s in build_events_catalog(EventService(SqliteEventStore(db)))
        if s.name == "read_recent_events"
    )
    sources = {e["source"] for e in read()["events"]}
    assert sources == {"github", "teams"}
    assert read(source="github")["count"] == 1
    assert read(source="teams")["count"] == 1


# --- Odporność mostu: jedno zatrute zdarzenie nie zrywa łączności ---------------------
def test_poisoned_event_does_not_break_connectivity(tmp_path):
    db = tmp_path / "events.db"
    events = EventService(SqliteEventStore(db))
    poisoned = _issue(7)
    poisoned["title"] = "zła\x00treść"  # znak sterujący z niezaufanej treści GitHuba
    client = _FakeGithubReadClient(issues=[poisoned, _issue(8, title="Czyste")])

    state: dict = {}
    ingested = asyncio.run(_poller(client, events, state=state).poll_once())
    sender = _FakeTeamsSender()
    asyncio.run(_notifier(EventService(SqliteEventStore(db)), sender).pump_once())

    assert ingested == 1  # zatrute pominięte, czyste przeszło
    assert state["issues_since"]  # watermark ruszył → most nie zakleszczony
    assert len(sender.channels) == 1 and "Czyste" in sender.channels[0][2]
