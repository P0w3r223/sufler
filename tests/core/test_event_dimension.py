"""Fundament ADR 0028: wymiar repo/project w zdarzeniu, dedup multi-repo, mapowanie repo→projekt.

Weryfikuje kluczową decyzję (R1): unikalność między repozytoriami zapewnia złożenie repo w
``external_id`` (``composite_external_id``), a NIE zmiana constraintu ``UNIQUE`` — dzięki temu
append-only i schemat magazynu pozostają nienaruszone, a stare bazy migrują przez ADD COLUMN.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from workmate.adapters.outbound.sqlite_events import SqliteEventStore
from workmate.adapters.outbound.yaml_projects_repo import YamlProjectsRepository
from workmate.core.application.services import ProjectsService
from workmate.core.domain.events import NewEvent, composite_external_id
from workmate.core.domain.models import Project


def _bez_echa(external_id: str, echo_kind: str) -> bool:
    """Predykat „magazyn nic o tym nie wie" — wołający bez magazynu podaje go JAWNIE.

    ``select_events`` wymaga tego argumentu bez wartości domyślnej (ADR 0071 decyzja 6): pominięty
    przez przeoczenie wyłączałby strażnika pętli w ciszy. Widoczna nazwa w wywołaniu mówi wprost,
    że TA sonda o echo nie pyta — a sonda, która pyta, podaje własny predykat.
    """
    return False


_WHEN = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def _ev(
    external_id: str, *, kind: str = "pr_opened", repo: str = "", project: str = ""
) -> NewEvent:
    return NewEvent(
        source="github",
        kind=kind,
        external_id=external_id,
        repo=repo,
        project=project,
        title="t",
        occurred_at=_WHEN,
    )


def test_composite_external_id_disambiguates_repos() -> None:
    assert composite_external_id("owner/a", "5") == "owner/a#5"
    assert composite_external_id("owner/b", "5") == "owner/b#5"
    assert composite_external_id("", "5") == "5"  # pojedyncze repo — wstecznie zgodne


def test_same_number_in_two_repos_does_not_dedup(tmp_path) -> None:
    store = SqliteEventStore(tmp_path / "events.db")
    a = store.append(_ev(composite_external_id("owner/a", "5"), repo="owner/a", project="pa"))
    b = store.append(_ev(composite_external_id("owner/b", "5"), repo="owner/b", project="pb"))
    assert a.id != b.id
    assert {e.external_id for e in store.recent()} == {"owner/a#5", "owner/b#5"}


def test_recent_and_read_since_filter_by_project(tmp_path) -> None:
    store = SqliteEventStore(tmp_path / "events.db")
    store.append(_ev("owner/a#1", repo="owner/a", project="pa"))
    store.append(_ev("owner/b#2", repo="owner/b", project="pb"))
    assert [e.project for e in store.recent(project="pa")] == ["pa"]
    assert [e.external_id for e in store.read_since(0, project="pb")] == ["owner/b#2"]


def test_migration_adds_columns_to_pre_0028_db(tmp_path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, kind TEXT NOT NULL, "
        "external_id TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '', "
        "title TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '', "
        "url TEXT NOT NULL DEFAULT '', occurred_at TEXT NOT NULL, "
        "ingested_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP), "
        "UNIQUE(source, external_id, kind))"
    )
    conn.execute(
        "INSERT INTO events(source, kind, external_id, occurred_at) "
        "VALUES ('github', 'issue_opened', '1', ?)",
        (_WHEN.isoformat(),),
    )
    conn.commit()
    conn.close()

    store = SqliteEventStore(path)  # otwarcie migruje: ADD COLUMN repo/project (ADR 0028)
    events = store.recent()
    assert len(events) == 1
    assert events[0].repo == "" and events[0].project == ""


class _FakeProjectsRepo:
    def __init__(self, projects: list[Project]) -> None:
        self._projects = projects

    def all(self) -> list[Project]:
        return list(self._projects)

    def get(self, key: str) -> Project | None:
        return next((p for p in self._projects if p.key == key), None)

    def status_record(self, key: str):  # niepotrzebne dla project_for_repo
        return None


class _EmptyNotesRepo:
    def all(self):
        return []


def test_project_for_repo_reverse_lookup() -> None:
    projects = [
        Project(
            key="workmate",
            company="biap",
            name="WM",
            description="",
            github_repos=["BIAP/PIWorkmate"],
        ),
        Project(key="scada", company="mpwik", name="S", description=""),
    ]
    service = ProjectsService(_FakeProjectsRepo(projects), _EmptyNotesRepo())
    assert service.project_for_repo("BIAP/PIWorkmate") == "workmate"
    assert service.project_for_repo("biap/piworkmate") == "workmate"  # bez rozróżniania wielkości
    assert service.project_for_repo("unknown/repo") is None
    assert service.project_for_repo("") is None


def test_registry_reads_external_mapping_and_is_backward_compatible(tmp_path) -> None:
    reg = tmp_path / "registry.yaml"
    reg.write_text(
        "projects:\n"
        "  - key: wm\n"
        "    company: biap\n"
        "    name: WM\n"
        "    github_repos: ['org/repo']\n"
        "    jira_project_key: WM\n"
        "  - key: legacy\n"  # wpis sprzed ADR 0028 — bez nowych pól
        "    company: x\n"
        "    name: L\n",
        encoding="utf-8",
    )
    by_key = {p.key: p for p in YamlProjectsRepository(reg).all()}
    assert by_key["wm"].github_repos == ["org/repo"]
    assert by_key["wm"].jira_project_key == "WM"
    assert by_key["legacy"].github_repos == []  # domyślne
    assert by_key["legacy"].jira_project_key is None


def test_github_selection_stamps_repo_and_project() -> None:
    from workmate.adapters.inbound.github import selection

    raw = {
        "number": 5,
        "title": "t",
        "created_at": "2026-07-15T10:00:00Z",
        "html_url": "http://gh/5",
        "user": {"login": "alice"},
    }
    events = selection.select_events(
        [raw], [], echo_seen=_bez_echa, watch_kinds=("issues",), repo="o/r", project="wm"
    )
    assert len(events) == 1
    # repo/project ostemplowane; external_id niezmienione (single-repo, ADR 0028/0029).
    assert (events[0].repo, events[0].project, events[0].external_id) == ("o/r", "wm", "5")


def test_map_pull_state_merged_closed_open() -> None:
    from workmate.adapters.inbound.github import selection

    merged = selection.map_pull_state(
        {
            "number": 7,
            "merged_at": "2026-07-17T10:00:00Z",
            "state": "closed",
            "title": "Feat",
            "html_url": "http://gh/7",
        }
    )
    assert merged is not None
    assert (merged.kind, merged.external_id, merged.actor) == ("pr_merged", "7#merged", "")

    closed = selection.map_pull_state(
        {
            "number": 8,
            "merged_at": None,
            "state": "closed",
            "closed_at": "2026-07-17T11:00:00Z",
            "title": "X",
            "html_url": "http://gh/8",
        }
    )
    assert closed is not None
    assert (closed.kind, closed.external_id) == ("pr_closed", "8#closed")

    assert selection.map_pull_state({"number": 9, "state": "open", "merged_at": None}) is None


def test_select_events_maps_pull_state_transitions() -> None:
    from workmate.adapters.inbound.github import selection

    events = selection.select_events(
        [],
        [],
        raw_pulls=[
            {
                "number": 7,
                "merged_at": "2026-07-17T10:00:00Z",
                "state": "closed",
                "title": "t",
                "html_url": "http://gh/7",
            }
        ],
        echo_seen=_bez_echa,
        watch_kinds=("pull_state",),
    )
    assert [e.kind for e in events] == ["pr_merged"]


def test_akcja_summary_zwija_zdarzenia_po_rodzaju(tmp_path) -> None:
    """Krok 5.2 (ADR 0009 paczki): ``get_project_activity`` to ``GitHub(action='activity')``."""
    from workmate.core.application.events import EventService
    from workmate.core.application.tools import build_activity_catalog

    store = SqliteEventStore(tmp_path / "events.db")
    for e in [
        _ev("o/r#1", kind="pr_opened", repo="o/r", project="wm"),
        _ev("o/r#1m", kind="pr_merged", repo="o/r", project="wm"),
        _ev("o/r#2", kind="issue_opened", repo="o/r", project="other"),
    ]:
        store.append(e)

    spec = build_activity_catalog(events=EventService(store))[0]
    assert spec.name == "Activity"
    result = spec.fn(action="summary", project="wm")
    assert result["project"] == "wm"
    assert result["event_count"] == 2
    assert result["by_kind"] == {"pr_opened": 1, "pr_merged": 1}
    assert result["latest_activity_at"] is not None


def test_project_status_enriched_with_event_activity(tmp_path) -> None:
    from datetime import date

    from workmate.core.application.events import EventService
    from workmate.core.domain.models import ProjectStatusRecord

    store = SqliteEventStore(tmp_path / "events.db")
    store.append(_ev("o/r#1", kind="pr_opened", repo="o/r", project="wm"))
    store.append(_ev("o/r#5", kind="ci_failure", repo="o/r", project="wm"))
    store.append(_ev("o/r#2", kind="issue_opened", repo="o/r", project="other"))

    class _Repo:
        def all(self):
            return [Project(key="wm", company="biap", name="WM", description="")]

        def get(self, key):
            return self.all()[0] if key == "wm" else None

        def status_record(self, key):
            if key != "wm":
                return None
            return ProjectStatusRecord(
                key="wm",
                status="active",
                health="green",
                phase="p",
                summary="s",
                last_updated=date(2026, 7, 1),
            )

    service = ProjectsService(_Repo(), _EmptyNotesRepo(), events=EventService(store))
    status = service.get_project_status("wm")
    assert status is not None
    assert status.recent_activity_count == 2  # tylko zdarzenia projektu wm
    assert status.failing_ci_count == 1
    assert status.latest_activity_at is not None


def _ev_o(external_id: str, *, when: datetime) -> NewEvent:
    """Zdarzenie o WSKAZANEJ chwili zajścia — do sondy „kolejność przyjęcia ≠ kolejność zajścia"."""
    return NewEvent(
        source="github",
        kind="pr_opened",
        external_id=external_id,
        repo="o/r",
        project="wm",
        title="t",
        occurred_at=when,
    )


def test_ostatnia_aktywnosc_to_najpozniejsze_ZAJSCIE_nie_ostatnie_przyjecie(tmp_path) -> None:
    """Regresja: oba miejsca brały ``items[0].occurred_at``, a ``recent`` sortuje po ``id``.

    Poller ma OSOBNE watermarki per typ zasobu (issues/comments/runs/reviews), więc starsze
    zdarzenie potrafi wejść do magazynu po nowszym. Pierwszy element okna zaniżał wtedy pole,
    na którym model buduje zdania w rodzaju „ostatnio nic się nie działo". Dotychczasowe sondy
    asertowały ``is not None``, więc odwrócenie kolejności ich nie ruszało.
    """
    from datetime import date

    from workmate.core.application.events import EventService
    from workmate.core.application.tools import build_activity_catalog
    from workmate.core.domain.models import ProjectStatusRecord

    pozniej = datetime(2026, 7, 1, 10, 5, tzinfo=UTC)
    wczesniej = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    store = SqliteEventStore(tmp_path / "events.db")
    store.append(_ev_o("o/r#1", when=pozniej))  # przyjęte PIERWSZE, zaszło PÓŹNIEJ
    store.append(_ev_o("o/r#2", when=wczesniej))  # przyjęte DRUGIE, zaszło WCZEŚNIEJ

    spec = build_activity_catalog(events=EventService(store))[0]
    assert spec.fn(action="summary", project="wm")["latest_activity_at"] == pozniej.isoformat()

    class _Repo:
        def all(self):
            return [Project(key="wm", company="biap", name="WM", description="")]

        def get(self, key):
            return self.all()[0] if key == "wm" else None

        def status_record(self, key):
            return ProjectStatusRecord(
                key="wm",
                status="active",
                health="green",
                phase="p",
                summary="s",
                last_updated=date(2026, 7, 1),
            )

    service = ProjectsService(_Repo(), _EmptyNotesRepo(), events=EventService(store))
    status = service.get_project_status("wm")
    assert status is not None
    assert status.latest_activity_at == pozniej


def test_diff_branches_seeds_then_detects_push_and_delete() -> None:
    from workmate.adapters.inbound.github import selection

    raw1 = [{"name": "main", "commit": {"sha": "aaa"}}, {"name": "feat", "commit": {"sha": "bbb"}}]
    # Pierwsza runda: SEED — zero zdarzeń, mapa zapisana.
    events, heads = selection.diff_branches(raw1, None, repo="o/r", project="wm", occurred_at=_WHEN)
    assert events == []
    assert heads == {"main": "aaa", "feat": "bbb"}

    # Druga runda: feat dostał nowy SHA (push); main bez zmian → bez zdarzenia.
    raw2 = [{"name": "main", "commit": {"sha": "aaa"}}, {"name": "feat", "commit": {"sha": "ccc"}}]
    events2, heads2 = selection.diff_branches(
        raw2, heads, repo="o/r", project="wm", occurred_at=_WHEN
    )
    assert [(e.kind, e.external_id) for e in events2] == [("branch_pushed", "feat@ccc")]
    assert heads2 == {"main": "aaa", "feat": "ccc"}

    # Trzecia runda: feat usunięta.
    raw3 = [{"name": "main", "commit": {"sha": "aaa"}}]
    events3, _ = selection.diff_branches(raw3, heads2, repo="o/r", project="wm", occurred_at=_WHEN)
    assert [(e.kind, e.external_id) for e in events3] == [("branch_deleted", "feat@ccc#deleted")]
    assert all(e.project == "wm" and e.repo == "o/r" and e.actor == "" for e in events3)
