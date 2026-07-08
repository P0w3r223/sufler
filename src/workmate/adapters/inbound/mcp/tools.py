"""Rejestracja czterech narzędzi Fazy 1 na serwerze FastMCP.

Narzędzia to cienkie "opakowania": walidują wejście przez sygnatury (FastMCP
generuje z nich schemat), wołają serwis aplikacyjny i zwracają dane w postaci
serializowalnej do JSON. Cała logika mieszka w rdzeniu — tutaj jest wyłącznie
tłumaczenie protokołu.

Na granicy łapiemy ``RepositoryError`` (błąd danych) i zwracamy ``{"error": ...}``,
żeby pojedyncza wadliwa notatka nie wywróciła całego serwera. Wyjątki nieznane
świadomie NIE są łapane — niech wypłyną jako błąd, bo oznaczają defekt kodu.

Opisy narzędzi są zwięzłe i zaczynają się od słów kluczowych — Claude Code
skraca opisy do ~2 KB, a wyszukiwarka narzędzi dopasowuje po pierwszych słowach.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from workmate.core.application.services import (
    NotesService,
    NotesWriteService,
    ProjectsService,
)
from workmate.core.domain.models import NoteMetadata
from workmate.core.errors import RepositoryError, WorkMateError


def register_tools(
    mcp: FastMCP,
    notes: NotesService,
    projects: ProjectsService,
    *,
    write_service: NotesWriteService | None = None,
) -> None:
    """Zarejestruj narzędzia, wstrzykując serwisy przez domknięcia.

    ``write_service`` jest opcjonalne: gdy ``None``, drzwi wystawiają wyłącznie
    narzędzia odczytu (profil uprawnień per drzwi, Bramka 2 / ADR 0006). Gdy
    podane, dochodzi mutujące ``save_note``.
    """

    @mcp.tool()
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

    @mcp.tool()
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

    @mcp.tool()
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

    @mcp.tool()
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

    if write_service is None:
        return

    @mcp.tool()
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
