"""Złożenie one-pagera projektu (ADR 0051, F4) — read-only, deterministyczne.

``ProjectBriefService.brief`` komponuje DWA istniejące odczyty w jeden ``ProjectBrief``:
``ProjectsService.get_project_status`` (pełna synteza: rejestr + fakty z notatek + aktywność
GitHub, ADR 0029) oraz OSTATNIE notatki z ``NotesService.search_notes`` (puste zapytanie →
wszystkie posortowane po dacie malejąco). Bez LLM, bez zapisu, bez nowego narzędzia MCP — sama
projekcja przechowanych faktów. Nieznany projekt → ``None`` (router zamieni na czytelną odmowę).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from workmate.core.domain.project_brief import DEFAULT_BRIEF_NOTES, ProjectBrief

if TYPE_CHECKING:
    from workmate.core.application.services import NotesService, ProjectsService


class ProjectBriefService:
    """Buduje ``ProjectBrief`` dla projektu z serwisów ODCZYTU notatek i projektów."""

    def __init__(
        self,
        notes: NotesService,
        projects: ProjectsService,
        *,
        notes_limit: int = DEFAULT_BRIEF_NOTES,
    ) -> None:
        self._notes = notes
        self._projects = projects
        self._notes_limit = notes_limit

    def brief(self, project: str) -> ProjectBrief | None:
        """Złóż one-pager projektu albo ``None``, gdy projekt nie istnieje w rejestrze.

        ``project`` to KLUCZ z rejestru (zaufany argument wzmianki, ADR 0009 §3), nie treść wątku.
        Puste zapytanie do ``search_notes`` zwraca notatki projektu posortowane po dacie malejąco,
        więc pierwsze ``notes_limit`` to te NAJŚWIEŻSZE.
        """
        status = self._projects.get_project_status(project)
        if status is None:
            return None
        recent = self._notes.search_notes("", project=project, limit=self._notes_limit)
        return ProjectBrief(status=status, recent_notes=tuple(recent))
