"""Katalog odczytu bazy wiedzy — trzy narzędzia wspólne dla drzwi MCP i agenta bez powłoki."""

from __future__ import annotations

from typing import Any

from workmate.core.application.services import (
    NotesService,
    ProjectsService,
)
from workmate.core.application.tools.naming import przemianuj_na_konwencje_agenta
from workmate.core.application.tools.spec import ToolSpec, _envelope


def build_notes_read_catalog(notes: NotesService, projects: ProjectsService) -> list[ToolSpec]:
    """Trzy narzędzia ODCZYTU bazy wiedzy: ``search_notes``, ``get_note``, ``list_projects``.

    Wydzielone z ``build_tool_catalog`` (którego są początkiem, bajt w bajt — pilnuje tego
    golden-test powierzchni MCP), bo mają DWÓCH konsumentów o różnym losie. Na drzwiach MCP
    zostają na zawsze: sesja Claude Code nie ma naszego wykonawcy, więc to jej jedyna droga
    do notatek. W runtime agenta wchodzą WARUNKOWO — tylko gdy powłoka jest niedostępna.

    Warunek jest istotą sprawy, a nie ostrożnością. ADR 0009 zdejmuje te trzy narzędzia
    z agenta, bo „powłoka je robi" — ale ``WORKMATE_ENABLE_SHELL`` jest domyślnie WYŁĄCZONA.
    (Wymóg „kanałów z wzajemnie zaufanymi uczestnikami" z ADR 0010 zniósł infra ADR 0012:
    wykonawca stoi PER ROZMOWĘ i widzi wyłącznie swój podkatalog brudnopisu, więc izolacja
    jest granicą montażu, a nie umową między ludźmi na kanale.) Bez powłoki bariera
    z kryterium ADR 0009 istnieje: agent nie ma ŻADNEJ drogi do bazy wiedzy. Bezwarunkowe
    cięcie zabrałoby produkcji zdolność, wokół której zbudowany jest produkt.
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

        def build() -> dict[str, Any]:
            results = notes.search_notes(
                query, project=project, participant=participant, limit=limit
            )
            return {
                "query": query,
                "count": len(results),
                "results": [r.model_dump(mode="json") for r in results],
            }

        return _envelope(build)

    def get_note(note_id: str) -> dict[str, Any]:
        """Pobierz pełną treść jednej notatki po jej identyfikatorze.

        Identyfikator ma postać ``<firma>/<projekt>/<plik-bez-rozszerzenia>``,
        np. 'mpwik/scada-integration/2025-06-12-przeglad-api-scada' (z wyników search_notes).
        """

        def build() -> dict[str, Any]:
            note = notes.get_note(note_id)
            if note is None:
                return {"error": f"Notatka nie istnieje: {note_id}"}
            return note.model_dump(mode="json")

        return _envelope(build)

    def list_projects() -> dict[str, Any]:
        """Wypisz projekty pionu dostępne w bazie wiedzy (klucz, nazwa, opis)."""

        def build() -> dict[str, Any]:
            items = projects.list_projects()
            return {
                "count": len(items),
                "projects": [p.model_dump(mode="json") for p in items],
            }

        return _envelope(build)

    return [
        ToolSpec("search_notes", search_notes.__doc__ or "", search_notes, taints=False),
        ToolSpec("get_note", get_note.__doc__ or "", get_note, taints=False),
        ToolSpec("list_projects", list_projects.__doc__ or "", list_projects, taints=False),
    ]


def build_agent_notes_read_catalog(
    notes: NotesService, projects: ProjectsService
) -> list[ToolSpec]:
    """Ta sama trójka odczytu co na drzwiach MCP, pod nazwami konwencji agenta (ADR 0068 §3).

    Zachowanie ma JEDNO źródło: sygnatura i ciało pochodzą z ``build_notes_read_catalog``,
    a opis z tego samego docstringa, przepuszczonego przez mapę nazw
    (``przemianuj_na_konwencje_agenta``). Osobna funkcja, a nie parametr tamtej, bo tamta jest
    bajt w bajt początkiem ``build_tool_catalog`` i golden-test MCP porównuje jej wynik wprost —
    parametr byłby zaproszeniem do przekazania go z drzwi MCP.
    """
    return [
        przemianuj_na_konwencje_agenta(spec) for spec in build_notes_read_catalog(notes, projects)
    ]
