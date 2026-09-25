"""Testy pętli pollera GitHub (poll_once) na atrapach — ingest, self-skip, watermark, dedup."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

from sufler.adapters.inbound.github.poller import GithubPoller, _iso_z
from sufler.core.application.events import EventService
from sufler.core.domain.events import Event, NewEvent

_WHEN = datetime(2026, 7, 15, tzinfo=UTC)


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
        self.login_calls = 0  # sonda: poller nie ma już pytać o konto PAT
        self.since_seen: list = []
        self.reviews_seen: list = []  # numery PR odpytane o recenzje

    def authenticated_login(self) -> str:
        self.login_calls += 1
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


def _run(
    run_id: int,
    *,
    conclusion: str = "success",
    attempt: int = 1,
    updated: str = "2026-07-15T13:00:00Z",
) -> dict:
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


def _review(
    review_id: int,
    *,
    state: str = "APPROVED",
    login: str = "alice",
    pr: int = 12,
    submitted: str = "2026-07-15T12:00:00Z",
) -> dict:
    return {
        "id": review_id,
        "state": state,
        "body": "recenzja",
        "html_url": f"https://github.com/o/r/pull/{pr}#pullrequestreview-{review_id}",
        "user": {"login": login},
        "submitted_at": submitted,
    }


def _poller(
    client,
    store,
    *,
    state=None,
    watch=("issues", "comments"),
    persist=None,
    stop=None,
    heartbeat=None,
):
    return GithubPoller(
        client,
        EventService(store),
        owner="o",
        repo="r",
        watch_kinds=watch,
        state=state if state is not None else {},
        persist=persist if persist is not None else (lambda s: None),
        poll_interval=1,
        per_page=50,
        stop=stop,
        heartbeat=heartbeat,
    )


def test_poll_once_ingests_new_events():
    store = _FakeStore()
    client = _FakeClient(issues=[_issue(1), _issue(2)])
    ingested = asyncio.run(_poller(client, store).poll_once())
    assert ingested == 2
    assert {r.external_id for r in store.rows} == {"1", "2"}


def test_run_finishes_current_cycle_then_exits_on_stop():
    """Graceful shutdown (R1): stop kończy pętlę PO jednej rundzie i zapisie, bez zapętlenia."""
    stop = asyncio.Event()
    persisted: list = []
    poller = _poller(
        _FakeClient(),
        _FakeStore(),
        persist=lambda s: persisted.append(dict(s)),
        stop=stop,
    )
    calls = 0

    async def _one_round():
        nonlocal calls
        calls += 1
        poller._persist(poller._state)  # runda utrwala stan (jak poll_once)
        stop.set()  # sygnał przychodzi w trakcie rundy

    poller.poll_once = _one_round  # type: ignore[method-assign]

    asyncio.run(asyncio.wait_for(poller.run(), timeout=5))

    assert calls == 1  # dokładnie jedna runda — pętla nie kręci się w kółko
    assert persisted  # stan zapisany przed wyjściem


def test_run_beats_heartbeat_after_successful_round():
    """Puls żywotności (R5) bije PO udanej rundzie — sygnał 'poller pracuje' dla healthchecku."""
    stop = asyncio.Event()
    beats = 0

    def _beat():
        nonlocal beats
        beats += 1

    poller = _poller(_FakeClient(), _FakeStore(), stop=stop, heartbeat=_beat)

    async def _one_round():
        stop.set()  # zakończ pętlę po tej rundzie
        return 0

    poller.poll_once = _one_round  # type: ignore[method-assign]
    asyncio.run(asyncio.wait_for(poller.run(), timeout=5))

    assert beats == 1  # dokładnie jeden puls za jedną udaną rundę


def test_run_skips_heartbeat_when_round_fails():
    """Jałowa pętla (runda rzuca w kółko) NIE bije pulsu — plik się starzeje, wykryje to check."""
    stop = asyncio.Event()
    beats = 0

    def _beat():
        nonlocal beats
        beats += 1

    poller = _poller(_FakeClient(), _FakeStore(), stop=stop, heartbeat=_beat)

    async def _failing_round():
        stop.set()  # zakończ pętlę po tej (nieudanej) rundzie
        raise RuntimeError("token nie do odnowienia")

    poller.poll_once = _failing_round  # type: ignore[method-assign]
    asyncio.run(asyncio.wait_for(poller.run(), timeout=5))

    assert beats == 0  # runda padła → brak pulsu


def test_poll_once_nie_pomija_juz_po_koncie_autora():
    """ODWRÓCONE 2026-09-07 (ADR 0071 decyzja 6). Do tego dnia issue autorstwa konta PAT było
    pomijane — na przesłance „nasze konto ⇒ nasze narzędzie", zmierzonej jako fałszywa.

    W tym repozytorium konto bota założyło osiem zgłoszeń, z czego przez narzędzie tylko dwa;
    sześć powstało `gh` CLI i przez WWW. Filtr po koncie zjadał wszystkie osiem, a echo miały
    dwa — pozostałych sześciu nie było nigdzie.
    """
    store = _FakeStore()
    client = _FakeClient(issues=[_issue(1, login="bot"), _issue(2, login="alice")])

    ingested = asyncio.run(_poller(client, store).poll_once())

    assert ingested == 2
    assert {r.external_id for r in store.rows} == {"1", "2"}


def test_poll_once_pomija_to_co_zapisaly_NASZE_DRZWI(monkeypatch):
    """Strażnik pętli po ECHU: pomijamy wtedy i tylko wtedy, gdy w magazynie leży ślad, który
    mogły zostawić wyłącznie nasze drzwi zapisu (``teams`` + ``github_issue_created``).

    Sonda kładzie echo do magazynu ręcznie, bo drzwi zapisu żyją w innym module — chodzi o to,
    żeby poller czytał magazyn, a nie o to, czy drzwi umieją pisać (od tego jest sonda kontraktu).
    """
    store = _FakeStore()
    store.append(
        NewEvent(
            source="teams",
            kind="github_issue_created",
            external_id="1",
            title="Prośba z Teams",
            summary="",
            actor="",
            url="",
            occurred_at=datetime(2026, 7, 15, 10, 0, tzinfo=UTC),
        )
    )
    client = _FakeClient(issues=[_issue(1, login="alice"), _issue(2, login="alice")])

    ingested = asyncio.run(_poller(client, store).poll_once())

    assert ingested == 1  # issue 1 ma echo naszych drzwi → pominięte mimo cudzego autora
    assert {r.external_id for r in store.rows if r.source == "github"} == {"2"}


def test_poll_once_dedups_across_rounds():
    store = _FakeStore()
    client = _FakeClient(issues=[_issue(1), _issue(2)])
    poller = _poller(client, store)
    assert asyncio.run(poller.poll_once()) == 2
    assert asyncio.run(poller.poll_once()) == 0  # te same issue → dedup magazynu, 0 nowych
    assert len(store.rows) == 2


def test_poll_once_advances_watermark_after_ingest():
    state: dict = {}
    client = _FakeClient(
        issues=[
            _issue(1, updated="2026-07-15T10:00:00Z"),
            _issue(2, updated="2026-07-15T12:00:00Z"),
        ]
    )
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


def test_poller_nie_pyta_juz_o_konto_PAT():
    """``GET /user`` znika razem z filtrem po koncie (ADR 0071 decyzja 6).

    Warto zapisać, bo pusta wartość `SUFLER_GITHUB_SELF_LOGIN` NIE wyłączała dawniej filtru —
    kazała ustalić konto z `GET /user`, więc filtr zostawał włączony z kontem wykrytym
    automatycznie. To jest powód, dla którego incydent zdarzył się na flocie, na której tej
    zmiennej nie ustawiono w ogóle: „wyłączenie" jej nie wyłączało niczego.
    """
    client = _FakeClient(login="octocat")

    asyncio.run(_poller(client, _FakeStore()).poll_once())

    assert not hasattr(GithubPoller, "_resolve_self_login")
    assert client.login_calls == 0


def test_poll_once_ingests_ci_runs():
    store = _FakeStore()
    client = _FakeClient(runs=[_run(1, conclusion="success"), _run(2, conclusion="failure")])
    ingested = asyncio.run(_poller(client, store, watch=("ci",)).poll_once())
    assert ingested == 2
    assert {(r.kind, r.external_id) for r in store.rows} == {
        ("ci_success", "1#1"),
        ("ci_failure", "2#1"),
    }
    assert ("runs", None) in client.since_seen  # gałąź CI odpytała klienta


def test_poll_once_advances_runs_watermark():
    state: dict = {}
    client = _FakeClient(
        runs=[_run(1, updated="2026-07-15T13:00:00Z"), _run(2, updated="2026-07-15T18:00:00Z")]
    )
    asyncio.run(_poller(client, _FakeStore(), state=state, watch=("ci",)).poll_once())
    assert state["runs_since"] == "2026-07-15T18:00:00Z"


def test_fetch_reviews_only_queries_open_pull_requests():
    store = _FakeStore()
    client = _FakeClient(
        issues=[_pr(10, state="open"), _pr(11, state="closed"), _issue(12)],
        reviews_by_pr={10: [_review(1, login="alice")]},
    )
    ingested = asyncio.run(_poller(client, store, watch=("issues", "reviews")).poll_once())
    # Tylko otwarty PR #10 odpytany o recenzje (zamknięty PR i zwykłe issue pominięte).
    assert client.reviews_seen == [10]
    assert ingested >= 1
    assert any(r.kind == "pr_review" for r in store.rows)


def test_fetch_reviews_respects_cap():
    # Więcej otwartych PR niż cap — odpytujemy najwyżej _MAX_REVIEW_PRS.
    from sufler.adapters.inbound.github.poller import _MAX_REVIEW_PRS

    prs = [_pr(n, state="open") for n in range(1, _MAX_REVIEW_PRS + 6)]
    client = _FakeClient(issues=prs)
    asyncio.run(_poller(client, _FakeStore(), watch=("issues", "reviews")).poll_once())
    assert len(client.reviews_seen) == _MAX_REVIEW_PRS


def test_poll_once_advances_reviews_watermark_by_submitted_at():
    state: dict = {}
    client = _FakeClient(
        issues=[_pr(10, state="open")],
        reviews_by_pr={
            10: [
                _review(1, submitted="2026-07-15T12:00:00Z"),
                _review(2, submitted="2026-07-15T16:00:00Z"),
            ]
        },
    )
    asyncio.run(_poller(client, _FakeStore(), state=state, watch=("issues", "reviews")).poll_once())
    assert state["reviews_since"] == "2026-07-15T16:00:00Z"


def test_seed_initializes_all_four_watermarks():
    poller = _poller(
        _FakeClient(), _FakeStore(), watch=("issues", "comments", "pulls", "reviews", "ci")
    )
    poller._seed("2026-07-15T00:00:00Z")
    state = poller._state
    assert set(state) == {"issues_since", "comments_since", "runs_since", "reviews_since"}
    assert all(v == "2026-07-15T00:00:00Z" for v in state.values())


def test_iso_z_seeds_in_github_format_not_isoformat():
    """Seed watermarku musi mieć format GitHuba (``…Z``), nie ``isoformat`` (``+00:00``) — inaczej
    porównanie leksykograficzne watermarków CI/recenzji ze znacznikami GitHuba by się rozjechało."""
    seeded = _iso_z(datetime(2026, 7, 16, 10, 38, 26, 123456, tzinfo=UTC))
    assert seeded == "2026-07-16T10:38:26Z"  # bez ułamków sekund i bez „+00:00"


# --- gałęzie i cap recenzji: regresje audytu -----------------------------------


class _BranchClient(_FakeClient):
    """``_FakeClient`` + gałęzie i PR-y (port ma je, atrapa bazowa jeszcze nie miała)."""

    def __init__(self, *, branches=(), pulls=(), **kwargs):
        super().__init__(**kwargs)
        self._branches = list(branches)
        self._pulls = list(pulls)

    def list_branches(self, owner, repo, *, per_page=50):
        return self._branches

    def list_pulls(self, owner, repo, *, state="all", per_page=50):
        return self._pulls


def _branch(name: str, sha: str) -> dict:
    return {"name": name, "commit": {"sha": sha}}


class _ExplodingStore(_FakeStore):
    """Magazyn, który pada na zapisie inaczej niż ``WriteError`` (blokada SQLite, dysk)."""

    def append(self, event: NewEvent) -> Event:
        raise OSError("database is locked")


def test_branch_heads_do_not_advance_when_ingest_fails():
    """Regresja: mapa gałęzi to WATERMARK i musi się przesuwać PO ingest, jak wszystkie inne.

    Push/usunięcie wykrywamy RÓŻNICĄ wobec poprzedniej rundy, więc mapa zapisana przy nieudanym
    ingest kasowała zdarzenie bezpowrotnie — następna runda widziała już „bez zmian".
    """
    state = {"branch_heads": {"main": "aaa"}}
    client = _BranchClient(branches=[_branch("main", "bbb")])
    poller = _poller(client, _ExplodingStore(), state=state, watch=("branches",))

    with contextlib.suppress(OSError):
        asyncio.run(poller.poll_once())

    assert state["branch_heads"] == {"main": "aaa"}  # nietknięta — push da się ponowić


def test_branch_heads_advance_after_a_successful_ingest():
    state = {"branch_heads": {"main": "aaa"}}
    client = _BranchClient(branches=[_branch("main", "bbb")])
    store = _FakeStore()

    asyncio.run(_poller(client, store, state=state, watch=("branches",)).poll_once())

    assert state["branch_heads"] == {"main": "bbb"}
    assert [r.kind for r in store.rows] == ["branch_pushed"]


def test_full_branch_page_is_not_read_as_a_wave_of_deletions():
    """Regresja: ciche ucięcie paginacji wyglądało jak usunięcie gałęzi — i to TRWALE.

    ``events.db`` jest append-only, więc zmyślone ``branch_deleted`` zostaje w nim i w digestach
    na zawsze. Pełne wiadro (``per_page × strony``) traktujemy więc jako „mogło być więcej".
    """
    strona = [_branch(f"feat/{i}", f"sha{i}") for i in range(500)]  # per_page 50 × 10 stron
    state = {"branch_heads": {**{f"feat/{i}": f"sha{i}" for i in range(500)}, "stara": "zzz"}}
    store = _FakeStore()

    asyncio.run(
        _poller(_BranchClient(branches=strona), store, state=state, watch=("branches",)).poll_once()
    )

    assert store.rows == []  # ani jednego zmyślonego branch_deleted
    assert state["branch_heads"]["stara"] == "zzz"  # gałąź spoza strony zostaje w mapie


def test_review_cap_keeps_the_freshest_prs_not_the_stalest():
    """Regresja: cap tnie z KOŃCA listy — ``/issues`` jedzie ``direction=asc``.

    Cięcie z przodu odrzucało PR-y NAJŚWIEŻSZE, a ``reviews_since`` awansowało tak czy tak;
    filtr recenzji jest kliencki i wykluczający, więc te recenzje ginęły na stałe.
    """
    prs = [_pr(n) for n in range(1, 31)]  # rosnąco po ``updated`` (jak GitHub przy asc)
    client = _BranchClient(issues=prs)

    asyncio.run(_poller(client, _FakeStore(), watch=("issues", "reviews")).poll_once())

    assert client.reviews_seen == list(range(11, 31))  # 20 NAJŚWIEŻSZYCH, nie 1..20


def test_page_budget_of_the_poller_matches_the_client_it_describes():
    """``_CLIENT_MAX_PAGES`` to ODWZOROWANIE prywatnej stałej klienta, przez granicę in/out.

    Rozjazd nie wywraca niczego głośno i na tym polega jego cena: heurystyka ucięcia
    (``_branches_truncated``) przestaje trafiać w pełne wiadro, ``diff_branches`` znów orzeka
    o usunięciach z niepełnej listy, a ``events.db`` jest append-only — zmyślone
    ``branch_deleted`` zostaje w nim i w digestach na zawsze. Import stałej z adaptera
    WYJŚCIOWEGO byłby sprzęgnięciem, którego port dziś nie ma; asercja wiąże obie kopie
    bez sprzęgania kodu.
    """
    from sufler.adapters.inbound.github import poller as poller_module
    from sufler.adapters.outbound.github_api import _MAX_PAGES

    assert poller_module._CLIENT_MAX_PAGES == _MAX_PAGES
