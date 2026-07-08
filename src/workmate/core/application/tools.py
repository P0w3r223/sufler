"""Jednoźródłowy katalog narzędzi (Faza 2, ADR 0008).

``ToolSpec`` niesie nazwę, opis i typowaną funkcję ``fn`` nad serwisami rdzenia.
Oba drzwi wywodzą się z tego samego katalogu: adapter MCP rejestruje ``fn`` na
FastMCP (schemat generowany z sygnatury — bez zmiany zamrożonego kontraktu, patrz
golden-test ``test_mcp_tool_surface``), a adapter agenta wyprowadza schemat
Anthropic z tej samej ``fn``. Bramkowanie zapisu per drzwi (ADR 0006) zachowane:
``save_note`` wchodzi do katalogu tylko przy podanym ``write_service``.

Funkcje narzędzi to cienkie opakowania serwisów: na granicy łapią ``RepositoryError``
/ ``WriteError`` i zwracają ``{"error": ...}`` (żeby jedna wadliwa dana nie
wywróciła serwera); wyjątki nieznane świadomie wypływają jako defekt kodu.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import ValidationError

from workmate.core.application.services import (
    NotesService,
    NotesWriteService,
    ProjectsService,
)
from workmate.core.domain.models import NoteMetadata
from workmate.core.errors import RepositoryError, WorkMateError


@dataclass(frozen=True)
class ToolSpec:
    """Transport-neutralna definicja narzędzia: nazwa, opis i funkcja nad serwisami."""

    name: str
    description: str
    fn: Callable[..., dict[str, Any]]


def build_tool_catalog(
    notes: NotesService,
    projects: ProjectsService,
    *,
    write_service: NotesWriteService | None = None,
) -> list[ToolSpec]:
    """Zbuduj katalog narzędzi nad serwisami.

    Zwraca 4 narzędzia odczytu zawsze; ``save_note`` dokłada tylko, gdy podano
    ``write_service`` (profil uprawnień per drzwi, ADR 0006) — dokładnie tak jak
    ``register_tools(write_service=None)`` na drzwiach MCP.
    """

    def search_notes(
        query: str,
        project: str | None = None,
        participant: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Przeszukaj notatki ze spotkań po słowach kluczowych i metadanych.

        Zwraca listę dopasowań (metadane + fragment), posortowaną po trafności.
        Opcjonalne filtry: ``project`` (klucz projektu, np. 'scada-integration') oraz
        ``participant`` (fragment nazwiska uczestnika).
        """
        try:
            results = notes.search_notes(
                query, project=project, participant=participant, limit=limit
            )
        except RepositoryError as exc:
            return {"error": str(exc)}
        return {
            "query": query,
            "count": len(results),
            "results": [r.model_dump(mode="json") for r in results],
        }

    def get_note(note_id: str) -> dict[str, Any]:
        """Pobierz pełną treść jednej notatki po jej identyfikatorze.

        Identyfikator ma postać ``<firma>/<projekt>/<plik-bez-rozszerzenia>``,
        np. 'mpwik/scada-integration/2025-06-12-przeglad-api-scada' (z wyników search_notes).
        """
        try:
            note = notes.get_note(note_id)
        except RepositoryError as exc:
            return {"error": str(exc)}
        if note is None:
            return {"error": f"Notatka nie istnieje: {note_id}"}
        return note.model_dump(mode="json")

    def list_projects() -> dict[str, Any]:
        """Wypisz projekty pionu dostępne w bazie wiedzy (klucz, nazwa, opis)."""
        try:
            items = projects.list_projects()
        except RepositoryError as exc:
            return {"error": str(exc)}
        return {
            "count": len(items),
            "projects": [p.model_dump(mode="json") for p in items],
        }

    def get_project_status(project: str) -> dict[str, Any]:
        """Zwróć status projektu: stan zadeklarowany + syntezę z notatek.

        ``project`` to klucz projektu (np. 'workmate'). W odpowiedzi m.in. firma,
        zdrowie, faza, podsumowanie oraz liczba notatek i otwartych action items.
        """
        try:
            status = projects.get_project_status(project)
        except RepositoryError as exc:
            return {"error": str(exc)}
        if status is None:
            return {"error": f"Projekt nie istnieje: {project}"}
        return status.model_dump(mode="json")

    catalog = [
        ToolSpec("search_notes", search_notes.__doc__ or "", search_notes),
        ToolSpec("get_note", get_note.__doc__ or "", get_note),
        ToolSpec("list_projects", list_projects.__doc__ or "", list_projects),
        ToolSpec("get_project_status", get_project_status.__doc__ or "", get_project_status),
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
        try:
            metadata = NoteMetadata(
                title=title,
                project=project,
                date=date,
                participants=participants or [],
                decisions=decisions or [],
                action_items=action_items or [],
                open_questions=open_questions or [],
                tags=tags or [],
            )
            note = write_service.save_note(metadata, body)
        except (WorkMateError, ValidationError) as exc:
            return {"error": str(exc)}
        return {"saved": True, "id": note.id, "path": f"{note.id}.md"}

    catalog.append(ToolSpec("save_note", save_note.__doc__ or "", save_note))
    return catalog
