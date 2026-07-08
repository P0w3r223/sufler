"""Testy bramkowania narzędzia zapisu na drzwiach MCP (``register_tools``).

``save_note`` jest mutujące i musi być rejestrowane tylko, gdy drzwi mają
włączony zapis (``write_service`` podane) — profil uprawnień per drzwi (Bramka 2,
ADR 0006). Sprawdzamy też, że ścieżka błędu na granicy zwraca ``{"error": ...}``
zamiast wywracać serwer.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from mcp.server.fastmcp import FastMCP

from tests.conftest import (
    FakeNotesRepository,
    FakeNotesWriter,
    FakeProjectsRepository,
)
from workmate.adapters.inbound.mcp.tools import register_tools
from workmate.core.application.services import (
    NotesService,
    NotesWriteService,
    ProjectsService,
)
from workmate.core.domain.models import Project

_READ_TOOLS = {"search_notes", "get_note", "list_projects", "get_project_status"}


def _projects_repo() -> FakeProjectsRepository:
    return FakeProjectsRepository(
        [
            Project(
                key="scada-integration",
                company="mpwik",
                name="Integracja SCADA MPWiK",
                description="Integracja MPWiK",
            )
        ],
        records={},
    )


def _register(*, with_write: bool) -> FastMCP:
    notes_repo = FakeNotesRepository([])
    projects_repo = _projects_repo()
    write_service = (
        NotesWriteService(FakeNotesWriter(), projects_repo) if with_write else None
    )
    mcp = FastMCP("test")
    register_tools(
        mcp,
        NotesService(notes_repo),
        ProjectsService(projects_repo, notes_repo),
        write_service=write_service,
    )
    return mcp


def _tool_names(mcp: FastMCP) -> set[str]:
    return {t.name for t in mcp._tool_manager.list_tools()}


def _tool_fn(mcp: FastMCP, name: str) -> Callable[..., dict[str, Any]]:
    return next(t.fn for t in mcp._tool_manager.list_tools() if t.name == name)


def test_save_note_not_registered_without_write_service():
    names = _tool_names(_register(with_write=False))

    assert "save_note" not in names
    assert names >= _READ_TOOLS  # narzędzia odczytu zawsze obecne


def test_save_note_registered_with_write_service():
    names = _tool_names(_register(with_write=True))

    assert "save_note" in names
    assert names >= _READ_TOOLS


def test_save_note_tool_happy_path_returns_saved_payload():
    save_note = _tool_fn(_register(with_write=True), "save_note")

    result = save_note(
        title="Przeglad API",
        project="scada-integration",
        date=date(2025, 6, 12),
        body="Treść.",
    )

    assert result == {
        "saved": True,
        "id": "mpwik/scada-integration/2025-06-12-przeglad-api",
        "path": "mpwik/scada-integration/2025-06-12-przeglad-api.md",
    }


def test_save_note_tool_unknown_project_returns_error_dict():
    save_note = _tool_fn(_register(with_write=True), "save_note")

    result = save_note(
        title="Przeglad API",
        project="nieznany",
        date=date(2025, 6, 12),
        body="Treść.",
    )

    # WriteError złapany na granicy → czytelny błąd, nie wyjątek.
    assert "error" in result
    assert "saved" not in result
