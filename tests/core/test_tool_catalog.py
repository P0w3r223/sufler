"""Testy jednoźródłowego katalogu narzędzi (ADR 0008) — bramkowanie zapisu.

Katalog jest wspólnym źródłem dla drzwi MCP i runtime'u agenta; kluczowa
niezmiennik to bramka zapisu per drzwi (ADR 0006): ``save_note`` wchodzi tylko
przy ``write_service`` — dokładnie jak ``register_tools(write_service=None)``.
"""

from __future__ import annotations

from tests.conftest import (
    FakeNotesRepository,
    FakeNotesWriter,
    FakeProjectsRepository,
)
from workmate.core.application.services import (
    NotesService,
    NotesWriteService,
    ProjectsService,
)
from workmate.core.application.tools import build_tool_catalog
from workmate.core.domain.models import Project

_READ_TOOLS = {"search_notes", "get_note", "list_projects", "get_project_status"}


def _services(*, with_write: bool):
    notes_repo = FakeNotesRepository([])
    projects_repo = FakeProjectsRepository(
        [Project(key="scada-integration", company="mpwik", name="X", description="")],
        records={},
    )
    notes = NotesService(notes_repo)
    projects = ProjectsService(projects_repo, notes_repo)
    write = NotesWriteService(FakeNotesWriter(), projects_repo) if with_write else None
    return notes, projects, write


def test_catalog_is_read_only_without_write_service():
    notes, projects, _ = _services(with_write=False)

    names = {spec.name for spec in build_tool_catalog(notes, projects)}

    assert names == _READ_TOOLS


def test_catalog_includes_save_note_with_write_service():
    notes, projects, write = _services(with_write=True)

    names = {spec.name for spec in build_tool_catalog(notes, projects, write_service=write)}

    assert names == _READ_TOOLS | {"save_note"}


def test_catalog_specs_carry_docstring_description_and_callable():
    notes, projects, _ = _services(with_write=False)

    specs = {spec.name: spec for spec in build_tool_catalog(notes, projects)}

    assert specs["search_notes"].description.startswith("Przeszukaj notatki")
    assert callable(specs["search_notes"].fn)
