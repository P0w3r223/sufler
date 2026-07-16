"""Testy serwisu auto-komentarza CI (CiAutoCommentService, ADR 0024 Faza 2) — na atrapach.

Sedno (celowe decyzje projektowe): jeden run×attempt = jeden komentarz (idempotencja przez znacznik
dedupowany magazynu), at-most-once przy awarii (komentarz świadomie tracony, nie ponawiany), filtr
``kind``/``source``, strażnik pętli (znacznik ``source="teams"``). Serwis pod testem jest REALNY,
podobnie REALNE ``EventService`` (na atrapie ``_FakeStore`` z prawdziwym dedupem) i REALNY
``GithubWriteService`` (na atrapie portu ``GithubWritePort``, która NOTUJE wywołania zapisu).
"""
from __future__ import annotations

from datetime import datetime, timezone

from workmate.core.application.ci_autocomment import CiAutoCommentService
from workmate.core.application.events import EventService
from workmate.core.application.github import GithubWriteService
from workmate.core.domain.events import Event, NewEvent
from workmate.core.errors import WriteError

_WHEN = datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc)
_REPO = "https://github.com/o/r"


class _FakeStore:
    """Atrapa ``EventStore`` w pamięci z dedupem po kluczu; ``read_since`` RESPEKTUJE ``source``.

    W przeciwieństwie do atrapy z ``test_poller`` filtrowanie po źródle jest tu prawdziwe — testy
    strażnika pętli/źródła sprawdzają realne zachowanie (serwis czyta tylko ``source="github"``).
    """

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
        return [
            r
            for r in self.rows
            if r.id > after_id and (source is None or r.source == source)
        ][:limit]

    def recent(self, *, source=None, limit=20):
        return list(reversed(self.rows))


class _RecordingPort:
    """Atrapa ``GithubWritePort`` — NOTUJE ``create_comment`` i zwraca dict jak realny GitHub.

    ``fail_comment=True`` symuluje błąd zapisu (rzuca ``WriteError``) — do testu odporności.
    """

    def __init__(self, *, fail_comment: bool = False) -> None:
        self.issues: list = []
        self.comments: list[tuple[str, str, int, str]] = []
        self._fail = fail_comment
        self._next_id = 100

    def create_issue(self, owner, repo, title, body, labels):
        self.issues.append((owner, repo, title, body, labels))
        return {"number": 1, "html_url": f"{_REPO}/issues/1", "created_at": "2026-07-15T13:00:00Z"}

    def create_comment(self, owner, repo, issue_number, body):
        if self._fail:
            raise WriteError("GitHub 500")
        self.comments.append((owner, repo, issue_number, body))
        cid = self._next_id
        self._next_id += 1
        return {
            "id": cid,
            "html_url": f"{_REPO}/pull/{issue_number}#issuecomment-{cid}",
            "created_at": "2026-07-15T13:05:00Z",
        }


def _wire(store, *, fail_comment: bool = False):
    """Złóż REALNE serwisy rdzenia na atrapach magazynu i portu zapisu."""
    events = EventService(store)
    port = _RecordingPort(fail_comment=fail_comment)
    writer = GithubWriteService(port, owner="o", repo="r", events=events)
    return events, writer, port


def _service(events, writer, *, cursor=0):
    # Trwałość kursora robi adapter (wątek pętli); serwis eksponuje pozycję przez ``cursor``.
    return CiAutoCommentService(events, writer, cursor=cursor)


def _seed_ci_failure(events, *, run_id="123", attempt="1", pr: int | None = 12) -> Event:
    """Wstrzyknij realistyczne zdarzenie ``ci_failure`` (kształt jak z ``selection.map_ci_run``)."""
    if pr is not None:
        url = f"{_REPO}/pull/{pr}"
        title = f"„CI” na PR #{pr}"
        summary = f"Przebieg: {_REPO}/actions/runs/{run_id}"
    else:
        url = f"{_REPO}/actions/runs/{run_id}"
        title = "„CI”"
        summary = ""
    event = events.ingest(
        NewEvent(
            source="github",
            kind="ci_failure",
            external_id=f"{run_id}#{attempt}",
            title=title,
            summary=summary,
            url=url,
            occurred_at=_WHEN,
        )
    )
    assert event is not None  # świeży seed nie jest duplikatem
    return event


# --- Happy path -------------------------------------------------------------


def test_happy_path_posts_comment_and_claims_marker():
    store = _FakeStore()
    events, writer, port = _wire(store)
    ev = _seed_ci_failure(events, pr=12)

    svc = _service(events, writer)
    posted = svc.process_once()

    assert posted == 1  # (c) process_once zwraca 1
    # (b) create_comment DOKŁADNIE RAZ, issue_number = numer PR z url, treść z szablonu.
    assert len(port.comments) == 1
    _owner, _repo, issue_number, body = port.comments[0]
    assert issue_number == 12
    assert ev.title in body
    assert "bez modelu" in body  # deterministyczny render (nie-LLM)
    # (a) znacznik zapisany do magazynu.
    markers = [r for r in store.rows if r.kind == "ci_autocomment"]
    assert len(markers) == 1
    assert markers[0].external_id == ev.external_id
    # (d) kursor przesunięty na id zdarzenia (trwałość utrwali adapter).
    assert svc.cursor == ev.id


def test_marker_uses_teams_source_and_dedicated_kind():
    # Strażnik pętli: znacznik ma ``source="teams"`` (notifier wypycha tylko github → nie odeśle)
    # i osobny ``kind`` do dedupu — potwierdzamy w zapisanym magazynie.
    store = _FakeStore()
    events, writer, _port = _wire(store)
    _seed_ci_failure(events, pr=12)

    _service(events, writer).process_once()

    marker = next(r for r in store.rows if r.kind == "ci_autocomment")
    assert marker.source == "teams"
    assert marker.kind == "ci_autocomment"


# --- Idempotencja -----------------------------------------------------------


def test_same_event_processed_twice_posts_one_comment():
    # Ponowne dostarczenie tego samego zdarzenia (np. restart procesu, kursor od 0) → znacznik
    # dedupowany, więc create_comment TYLKO RAZ (idempotencja nie zależy od kursora).
    store = _FakeStore()
    events, writer, port = _wire(store)
    _seed_ci_failure(events, run_id="123", attempt="1", pr=12)

    first = _service(events, writer, cursor=0).process_once()
    second = _service(events, writer, cursor=0).process_once()

    assert first == 1
    assert second == 0
    assert len(port.comments) == 1


def test_rerun_with_new_attempt_posts_second_comment():
    # Re-run dzieli ``run_id``, ale ma inny ``run_attempt`` → inny external_id (123#1 vs 123#2),
    # więc znacznik się nie dedupuje i DRUGI komentarz powstaje (re-run nie znika w dedupie).
    store = _FakeStore()
    events, writer, port = _wire(store)
    _seed_ci_failure(events, run_id="123", attempt="1", pr=12)
    _seed_ci_failure(events, run_id="123", attempt="2", pr=12)

    posted = _service(events, writer).process_once()

    assert posted == 2
    assert len(port.comments) == 2


# --- Porażka bez PR / filtr kind / source ----------------------------------


def test_ci_failure_without_pr_posts_nothing_but_advances_cursor():
    store = _FakeStore()
    events, writer, port = _wire(store)
    ev = _seed_ci_failure(events, run_id="99", pr=None)  # url przebiegu, bez ``/pull/``

    svc = _service(events, writer)
    posted = svc.process_once()

    assert posted == 0
    assert port.comments == []  # brak komentarza
    assert not any(r.kind == "ci_autocomment" for r in store.rows)  # brak znacznika
    assert svc.cursor == ev.id  # kursor i tak się przesuwa


def test_ignores_non_ci_failure_kinds():
    # issue_opened / ci_success / pr_comment (nawet z url ``/pull/``) są ignorowane; kursor
    # przesuwa się przez wszystkie zdarzenia (żeby nie utknąć na nieinteresujących).
    store = _FakeStore()
    events, writer, port = _wire(store)
    events.ingest(NewEvent(source="github", kind="issue_opened", external_id="1",
                           title="i", url=f"{_REPO}/issues/1", occurred_at=_WHEN))
    events.ingest(NewEvent(source="github", kind="ci_success", external_id="2#1",
                           title="ok", url=f"{_REPO}/pull/5", occurred_at=_WHEN))
    e3 = events.ingest(NewEvent(source="github", kind="pr_comment", external_id="3",
                                title="c", url=f"{_REPO}/pull/5", occurred_at=_WHEN))

    svc = _service(events, writer)
    posted = svc.process_once()

    assert posted == 0
    assert port.comments == []
    assert svc.cursor == e3.id  # kursor przez wszystkie (nie utyka na nieinteresujących)


def test_reads_only_github_source():
    # Serwis czyta wyłącznie ``source="github"``; zdarzenie ``source="teams"`` (np. echo zapisu)
    # o tym samym kind jest niewidoczne — potwierdza, że atrapa respektuje source (test #9).
    store = _FakeStore()
    events, writer, port = _wire(store)
    events.ingest(NewEvent(source="teams", kind="ci_failure", external_id="7#1",
                           title="t", url=f"{_REPO}/pull/7", occurred_at=_WHEN))
    ev = _seed_ci_failure(events, run_id="8", attempt="1", pr=8)

    svc = _service(events, writer)
    posted = svc.process_once()

    assert posted == 1
    assert port.comments[0][2] == 8  # tylko github-owe zdarzenie skomentowane
    assert svc.cursor == ev.id  # teams-owe nie trafiło do batcha


def test_starts_from_provided_cursor():
    # Serwis zaczyna od podanego kursora — zdarzenie <= cursor pomijane, save_cursor wołany z id.
    store = _FakeStore()
    events, writer, port = _wire(store)
    e1 = _seed_ci_failure(events, run_id="1", attempt="1", pr=1)
    e2 = _seed_ci_failure(events, run_id="2", attempt="1", pr=2)

    svc = _service(events, writer, cursor=e1.id)
    posted = svc.process_once()

    assert posted == 1
    assert port.comments[0][2] == 2  # tylko zdarzenie po kursorze
    assert svc.cursor == e2.id


# --- Odporność (best-effort, at-most-once) ---------------------------------


def test_comment_failure_does_not_crash_marker_persists_cursor_advances():
    # Gdy create_comment rzuci → process_once NIE wywala się, kursor się przesuwa, a znacznik
    # JUŻ istnieje (zaklepany przed komentarzem) → komentarz świadomie tracony, nie ponawiany.
    store = _FakeStore()
    events, writer, port = _wire(store, fail_comment=True)
    ev = _seed_ci_failure(events, pr=12)

    svc = _service(events, writer)
    posted = svc.process_once()

    assert posted == 0  # wyjątek połknięty, komentarz nie doszedł
    assert port.comments == []
    assert any(r.kind == "ci_autocomment" for r in store.rows)  # znacznik zaklepany
    assert svc.cursor == ev.id  # kursor przesunięty mimo błędu

    # Retry zdrowym pisarzem: znacznik dedupowany → komentarz NADAL nie powstaje (at-most-once).
    events2, writer2, port2 = _wire(store)
    _service(events2, writer2, cursor=0).process_once()
    assert port2.comments == []
