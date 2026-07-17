"""Fundament ADR 0028: wymiar repo/project w zdarzeniu, dedup multi-repo, mapowanie repo→projekt.

Weryfikuje kluczową decyzję (R1): unikalność między repozytoriami zapewnia złożenie repo w
``external_id`` (``composite_external_id``), a NIE zmiana constraintu ``UNIQUE`` — dzięki temu
append-only i schemat magazynu pozostają nienaruszone, a stare bazy migrują przez ADD COLUMN.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from workmate.adapters.outbound.sqlite_events import SqliteEventStore
from workmate.adapters.outbound.yaml_projects_repo import YamlProjectsRepository
from workmate.core.application.services import ProjectsService
from workmate.core.domain.events import NewEvent, composite_external_id
from workmate.core.domain.models import Project

_WHEN = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


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
        [raw], [], self_login="bot", watch_kinds=("issues",), repo="o/r", project="wm"
    )
    assert len(events) == 1
    # repo/project ostemplowane; external_id niezmienione (single-repo, ADR 0028/0029).
    assert (events[0].repo, events[0].project, events[0].external_id) == ("o/r", "wm", "5")
