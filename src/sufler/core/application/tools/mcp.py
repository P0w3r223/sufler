"""Katalog drzwi MCP — powierzchnia ZAMROŻONA golden-testem ``test_mcp_tool_surface``.

``build_tool_catalog`` jest jednocześnie routerem komend, więc woła go też strona
agenta: przed każdą zmianą sprawdź, kto jeszcze go używa (CLAUDE.md, reguła 6)."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import ValidationError

from sufler.core.application.services import (
    NotesService,
    NotesWriteService,
    ProjectsService,
)
from sufler.core.application.tools.notes_read import build_notes_read_catalog
from sufler.core.application.tools.spec import ToolSpec, _envelope
from sufler.core.domain.notes import build_note_metadata
from sufler.core.errors import SuflerError


def build_tool_catalog(
    notes: NotesService,
    projects: ProjectsService,
    *,
    write_service: NotesWriteService | None = None,
) -> list[ToolSpec]:
    """Zbuduj katalog narzędzi nad serwisami — POWIERZCHNIA DRZWI MCP (zamrożona).

    Zwraca 4 narzędzia odczytu zawsze; ``save_note`` dokłada tylko, gdy podano
    ``write_service`` (profil uprawnień per drzwi, ADR 0006) — dokładnie tak jak
    ``register_tools(write_service=None)`` na drzwiach MCP.

    Runtime agenta od kroku 5.4 (ADR 0009) tego katalogu NIE używa: składa własny
    z ``build_project_catalog`` i — gdy nie ma powłoki — ``build_agent_notes_read_catalog``
    (nazwy z ADR 0068; do tamtej rundy: ``build_notes_catalog`` i ``build_notes_read_catalog``).
    Konsolidacja przeprowadzona tutaj skasowałaby zdolności po stronie MCP zamiast
    przenieść je na powłokę, której tamte drzwi nie mają.
    """

    def get_project_status(project: str) -> dict[str, Any]:
        """Zwróć status projektu: stan zadeklarowany + syntezę z notatek.

        ``project`` to klucz projektu (np. 'workmate'). W odpowiedzi m.in. firma,
        zdrowie, faza, podsumowanie oraz liczba notatek i otwartych action items.
        """

        def build() -> dict[str, Any]:
            status = projects.get_project_status(project)
            if status is None:
                return {"error": f"Projekt nie istnieje: {project}"}
            return status.model_dump(mode="json")

        return _envelope(build)

    catalog = [
        *build_notes_read_catalog(notes, projects),
        ToolSpec(
            "get_project_status", get_project_status.__doc__ or "", get_project_status, taints=False
        ),
    ]

    if write_service is None:
        return catalog

    def save_note(
        title: str,
        project: str,
        date: date,
        body: str,
        participants: list[str] | None = None,
        decisions: list[str] | None = None,
        action_items: list[str] | None = None,
        open_questions: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Zapisz nową notatkę ze spotkania (ZAPIS — dodaje plik do bazy wiedzy).

        Wylicza miejsce zapisu z metadanych: firma z rejestru projektu, dalej
        <firma>/<projekt>/<data>-<slug tytułu>. Nigdy nie nadpisuje istniejącej
        notatki (przy kolizji dokłada sufiks). ``date`` w formacie YYYY-MM-DD;
        ``project`` musi istnieć w rejestrze (patrz list_projects).
        """

        def build() -> dict[str, Any]:
            metadata = build_note_metadata(
                title=title,
                project=project,
                date=date,
                participants=participants,
                decisions=decisions,
                action_items=action_items,
                open_questions=open_questions,
                tags=tags,
            )
            note = write_service.save_note(metadata, body)
            return {"saved": True, "id": note.id, "path": f"{note.id}.md"}

        return _envelope(build, errors=(SuflerError, ValidationError))

    catalog.append(ToolSpec("save_note", save_note.__doc__ or "", save_note, taints=False))
    return catalog
