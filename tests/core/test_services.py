"""Testy logiki serwisów (wyszukiwanie, odczyt, synteza statusu)."""
from __future__ import annotations

from datetime import date

from tests.conftest import FakeNotesRepository, FakeProjectsRepository
from workmate.core.application.services import NotesService, ProjectsService
from workmate.core.domain.models import Project, ProjectStatusRecord


def test_search_finds_by_query_in_body(sample_notes):
    service = NotesService(FakeNotesRepository(sample_notes))

    results = service.search_notes("SCADA")

    assert [r.id for r in results] == ["mpwik/scada-integration/2025-05-14-kickoff"]
    assert results[0].snippet  # fragment nie jest pusty


def test_search_matches_title_and_ranks_it_higher(sample_notes):
    service = NotesService(FakeNotesRepository(sample_notes))

    results = service.search_notes("API")

    # Notatka z "API" w tytule (waga wyższa) powinna być przed tą tylko z treści.
    assert results[0].id == "mpwik/scada-integration/2025-06-12-api"
    assert results[0].score > results[1].score


def test_search_filters_by_project(sample_notes):
    service = NotesService(FakeNotesRepository(sample_notes))

    results = service.search_notes("schemat", project="workmate")

    assert len(results) == 1
    assert results[0].project == "workmate"


def test_search_filters_by_participant(sample_notes):
    service = NotesService(FakeNotesRepository(sample_notes))

    results = service.search_notes("", participant="piotr")

    assert [r.id for r in results] == ["biap/workmate/2025-06-10-schemat"]


def test_empty_query_returns_all_sorted_by_date_desc(sample_notes):
    service = NotesService(FakeNotesRepository(sample_notes))

    results = service.search_notes("")

    dates = [r.date for r in results]
    assert dates == sorted(dates, reverse=True)
    assert len(results) == len(sample_notes)


def test_search_respects_limit(sample_notes):
    service = NotesService(FakeNotesRepository(sample_notes))

    results = service.search_notes("", limit=1)

    assert len(results) == 1


def test_get_note_returns_full_note(sample_notes):
    service = NotesService(FakeNotesRepository(sample_notes))

    note = service.get_note("biap/workmate/2025-06-10-schemat")

    assert note is not None
    assert note.metadata.title == "Schemat notatki"


def test_get_note_missing_returns_none(sample_notes):
    service = NotesService(FakeNotesRepository(sample_notes))

    assert service.get_note("nie/istnieje") is None


def _projects_service(sample_notes) -> ProjectsService:
    projects = [
        Project(
            key="scada-integration",
            company="mpwik",
            name="Integracja SCADA MPWiK",
            description="Integracja MPWiK",
        ),
        Project(
            key="workmate",
            company="biap",
            name="WorkMate",
            description="Asystent wiedzy",
        ),
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
        FakeProjectsRepository(projects, records),
        FakeNotesRepository(sample_notes),
    )


def test_list_projects_sorted_by_key(sample_notes):
    service = _projects_service(sample_notes)

    keys = [p.key for p in service.list_projects()]

    assert keys == ["scada-integration", "workmate"]


def test_project_status_synthesizes_from_notes(sample_notes):
    service = _projects_service(sample_notes)

    status = service.get_project_status("scada-integration")

    assert status is not None
    assert status.company == "mpwik"  # firma wyprowadzona z rejestru
    assert status.notes_count == 2  # dwie notatki scada-integration w próbce
    assert status.latest_note_date == date(2025, 6, 12)
    assert status.open_action_items == 3  # 2 + 1 action items
    assert status.name == "Integracja SCADA MPWiK"


def test_project_status_missing_returns_none(sample_notes):
    service = _projects_service(sample_notes)

    assert service.get_project_status("nieznany") is None
