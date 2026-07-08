"""Wspólne okablowanie runtime'u agenta dla drzwi inbound (CLI, Telegram, …).

Buduje ``AgentRuntime`` z tych samych serwisów i jednoźródłowego katalogu co drzwi
MCP. Profil zaufania per drzwi (ADR 0006) jest jawną flagą ``enable_write``:
zaufane drzwi (lokalne CLI) budują katalog READ+WRITE, mniej zaufane (Telegram) —
READ-ONLY (agent czyta, nie zapisuje). Import Claude API jest leniwy (w adapterze
outbound); brak extra ``agent`` daje ``ImportError``, który entry-point drzwi
zamienia na czytelny komunikat.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from workmate.adapters.outbound.markdown_notes_repo import MarkdownNotesRepository
from workmate.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
from workmate.adapters.outbound.yaml_projects_repo import YamlProjectsRepository
from workmate.core.agent.runtime import AgentRuntime
from workmate.core.application.services import (
    NotesService,
    NotesWriteService,
    ProjectsService,
)
from workmate.core.application.tools import build_tool_catalog

if TYPE_CHECKING:
    from workmate.config import AgentSettings, Settings


def build_agent_runtime(
    settings: Settings, agent_settings: AgentSettings, *, enable_write: bool
) -> AgentRuntime:
    """Zbuduj runtime: repozytoria → serwisy → katalog → klient LLM.

    ``enable_write`` steruje profilem zaufania drzwi: ``True`` → katalog z
    ``save_note`` (zaufane, np. lokalne CLI); ``False`` → katalog tylko do odczytu
    (mniej zaufane drzwi, np. Telegram — ADR 0006).
    """
    from workmate.adapters.outbound.anthropic_llm import AnthropicLLMClient

    notes_repo = MarkdownNotesRepository(settings.notes_dir)
    projects_repo = YamlProjectsRepository(settings.projects_registry)
    notes_service = NotesService(notes_repo)
    projects_service = ProjectsService(projects_repo, notes_repo)
    write_service = (
        NotesWriteService(MarkdownNotesWriter(settings.notes_dir), projects_repo)
        if enable_write
        else None
    )
    catalog = build_tool_catalog(notes_service, projects_service, write_service=write_service)
    return AgentRuntime(
        AnthropicLLMClient(agent_settings),
        catalog,
        max_tool_iterations=agent_settings.max_tool_iterations,
    )
