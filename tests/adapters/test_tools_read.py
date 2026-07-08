"""Testy cienkich opakowań 4 narzędzi ODCZYTU na drzwiach MCP (``register_tools``).

Serwisy pod spodem są testowane w ``tests/core/test_services.py``; tutaj sprawdzamy
wyłącznie warstwę adaptera: tłumaczenie ``RepositoryError`` na ``{"error": ...}``
(żeby wadliwe dane nie wywróciły serwera) oraz ścieżkę „nie istnieje" dla
``get_note`` i ``get_project_status``.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from mcp.server.fastmcp import FastMCP

from tests.conftest import FakeNotesRepository, FakeProjectsRepository
from workmate.adapters.inbound.mcp.tools import register_tools
from workmate.core.application.services import NotesService, ProjectsService
from workmate.core.domain.models import Note, Project, ProjectStatusRecord
from workmate.core.errors import RepositoryError


class _RaisingNotesRepository:
    """Atrapa ``NotesRepository``, która na każdej ścieżce podnosi ``RepositoryError``."""

    def all(self) -> list[Note]:
        raise RepositoryError("awaria odczytu notatek")

    def get(self, note_id: str) -> Note | None:
        raise RepositoryError("awaria odczytu notatki")


class _RaisingProjectsRepository:
    """Atrapa ``ProjectsRepository``, która na każdej ścieżce podnosi ``RepositoryError``."""

    def all(self) -> list[Project]:
        raise RepositoryError("awaria odczytu rejestru")

    def get(self, key: str) -> Project | None:
        raise RepositoryError("awaria odczytu rejestru")

    def status_record(self, key: str) -> ProjectStatusRecord | None:
        raise RepositoryError("awaria odczytu rejestru")


def _tool_fn(mcp: FastMCP, name: str) -> Callable[..., dict[str, Any]]:
    return next(t.fn for t in mcp._tool_manager.list_tools() if t.name == name)


def _register(notes: Any, projects: Any) -> FastMCP:
    mcp = FastMCP("test")
    register_tools(mcp, NotesService(notes), ProjectsService(projects, notes))
    return mcp


# --- RepositoryError na granicy → {"error": ...} zamiast wyjątku ----------------


def test_search_notes_wraps_repository_error():
    mcp = _register(_RaisingNotesRepository(), FakeProjectsRepository([], {}))
    search_notes = _tool_fn(mcp, "search_notes")

    result = search_notes(query="cokolwiek")

    assert "error" in result
    assert "results" not in result


def test_get_note_wraps_repository_error():
    mcp = _register(_RaisingNotesRepository(), FakeProjectsRepository([], {}))
    get_note = _tool_fn(mcp, "get_note")

    result = get_note(note_id="mpwik/scada-integration/x")

    assert "error" in result


def test_list_projects_wraps_repository_error():
    mcp = _register(FakeNotesRepository([]), _RaisingProjectsRepository())
    list_projects = _tool_fn(mcp, "list_projects")

    result = list_projects()

    assert "error" in result
    assert "projects" not in result


def test_get_project_status_wraps_repository_error():
    get_project_status = _tool_fn(
        _register(FakeNotesRepository([]), _RaisingProjectsRepository()), "get_project_status"
    )

    result = get_project_status(project="workmate")

    assert "error" in result


# --- Ścieżka „nie istnieje" → czytelny {"error": ...} ---------------------------


def test_get_note_not_found_returns_error():
    mcp = _register(FakeNotesRepository([]), FakeProjectsRepository([], {}))
    get_note = _tool_fn(mcp, "get_note")

    result = get_note(note_id="nie/ma/takiej")

    assert result == {"error": "Notatka nie istnieje: nie/ma/takiej"}


def test_get_project_status_not_found_returns_error():
    get_project_status = _tool_fn(
        _register(FakeNotesRepository([]), FakeProjectsRepository([], {})), "get_project_status"
    )

    result = get_project_status(project="nieznany")

    assert result == {"error": "Projekt nie istnieje: nieznany"}


# --- Happy path: kształt odpowiedzi bez błędu -----------------------------------


def test_search_notes_happy_path_shape():
    mcp = _register(FakeNotesRepository([]), FakeProjectsRepository([], {}))
    search_notes = _tool_fn(mcp, "search_notes")

    result = search_notes(query="x")

    assert result == {"query": "x", "count": 0, "results": []}


def test_read_tool_output_shapes_are_stable(sample_notes):
    """Kontrakt WYJŚĆ (Bramka 1): pinujemy kształt odpowiedzi pozostałych narzędzi odczytu.

    ``search_notes`` (wyżej) i ``save_note`` (test_tools_gating) już mają swoje
    asercje; tu domykamy ``list_projects`` / ``get_note`` / ``get_project_status``.
    """
    projects_repo = FakeProjectsRepository(
        [Project(key="scada-integration", company="mpwik", name="Integracja", description="d")],
        {
            "scada-integration": ProjectStatusRecord(
                key="scada-integration",
                status="active",
                health="green",
                phase="Faza 1",
                summary="W toku",
                last_updated=date(2025, 6, 26),
            )
        },
    )
    mcp = _register(FakeNotesRepository(sample_notes), projects_repo)

    projects = _tool_fn(mcp, "list_projects")()
    assert set(projects) == {"count", "projects"}
    assert set(projects["projects"][0]) == {"key", "company", "name", "description"}

    note = _tool_fn(mcp, "get_note")(note_id="mpwik/scada-integration/2025-06-12-api")
    assert set(note) == {"id", "metadata", "body"}

    status = _tool_fn(mcp, "get_project_status")(project="scada-integration")
    assert set(status) == {
        "key",
        "company",
        "name",
        "status",
        "health",
        "phase",
        "summary",
        "last_updated",
        "notes_count",
        "latest_note_date",
        "open_action_items",
    }
