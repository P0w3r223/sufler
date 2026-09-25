"""Testy złożenia one-pagera (``ProjectBriefService.brief``, F4, ADR 0051).

Serwis komponuje DWA istniejące odczyty (status + notatki), więc testujemy go na REALNYCH
``NotesService``/``ProjectsService`` nad atrapami repo (bez sieci/FS). Kluczowe niezmienniki:
nieznany projekt → ``None``; notatki to NAJŚWIEŻSZE tego projektu (nie cudzego); limit działa.
"""

from __future__ import annotations

from datetime import date

from sufler.core.application.project_brief import ProjectBriefService
from sufler.core.application.services import NotesService, ProjectsService
from sufler.core.domain.models import Project, ProjectStatusRecord
from tests.conftest import FakeNotesRepository, FakeProjectsRepository


def _projects_service(sample_notes) -> ProjectsService:
    projects = [
        Project(key="scada-integration", company="mpwik", name="Integracja SCADA", description="x"),
        Project(key="workmate", company="biap", name="Sufler", description="Asystent wiedzy"),
    ]
    records = {
        "scada-integration": ProjectStatusRecord(
            key="scada-integration",
            status="active",
            health="green",
            phase="Faza 1",
            summary="W toku",
            last_updated=date(2025, 6, 26),
        ),
        "workmate": ProjectStatusRecord(
            key="workmate",
            status="active",
            health="green",
            phase="Faza 1",
            summary="W toku",
            last_updated=date(2025, 6, 24),
        ),
    }
    return ProjectsService(
        FakeProjectsRepository(projects, records), FakeNotesRepository(sample_notes)
    )


def _service(sample_notes, *, notes_limit: int = 5) -> ProjectBriefService:
    return ProjectBriefService(
        NotesService(FakeNotesRepository(sample_notes)),
        _projects_service(sample_notes),
        notes_limit=notes_limit,
    )


def test_brief_composes_status_and_recent_notes(sample_notes):
    brief = _service(sample_notes).brief("scada-integration")

    assert brief is not None
    assert brief.status.key == "scada-integration"
    assert brief.status.company == "mpwik"
    # Notatki należą TYLKO do tego projektu (nie przeciekają z 'workmate').
    assert all(n.project == "scada-integration" for n in brief.recent_notes)
    assert brief.recent_notes, "projekt ma notatki — brief powinien je wylistować"


def test_brief_orders_notes_newest_first(sample_notes):
    brief = _service(sample_notes).brief("scada-integration")

    assert brief is not None
    dates = [n.date for n in brief.recent_notes]
    assert dates == sorted(dates, reverse=True)


def test_brief_unknown_project_returns_none(sample_notes):
    assert _service(sample_notes).brief("nie-ma-takiego") is None


def test_brief_respects_notes_limit(sample_notes):
    brief = _service(sample_notes, notes_limit=1).brief("scada-integration")

    assert brief is not None
    assert len(brief.recent_notes) == 1


def test_brief_project_without_notes_has_empty_list():
    # Projekt jest w rejestrze i ma status, ale bazę notatek zostawiamy PUSTĄ. Fixture
    # ``sample_notes`` był tu wcześniej w sygnaturze i nieużywany — sugerował, że test coś
    # z tych notatek bierze, podczas gdy budował repozytorium od zera.
    service = ProjectBriefService(NotesService(FakeNotesRepository([])), _projects_service([]))

    brief = service.brief("workmate")

    assert brief is not None
    assert brief.recent_notes == ()
    assert brief.status.notes_count == 0
