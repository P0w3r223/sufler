"""Rejestracja narzędzi na serwerze FastMCP z jednoźródłowego katalogu (ADR 0008).

Drzwi MCP to cienka pętla po ``build_tool_catalog``: FastMCP generuje schemat
każdego narzędzia z sygnatury ``fn`` — identycznie jak przed przebudową (pilnuje
tego golden-test ``tests/adapters/test_mcp_tool_surface.py``). Nazwa i opis
narzędzia pochodzą z ``fn`` (jej ``__name__`` i docstring), więc kontrakt 4+1
narzędzi (Bramka 1) pozostaje zamrożony i jednoźródłowy z runtime'em agenta.

Bramkowanie zapisu per drzwi (Bramka 2 / ADR 0006) zachowane: ``write_service=None``
→ katalog bez ``save_note`` → drzwi wystawiają wyłącznie narzędzia odczytu.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from workmate.core.application.events import EventService
from workmate.core.application.services import (
    NotesService,
    NotesWriteService,
    ProjectsService,
)
from workmate.core.application.tools import build_events_since_catalog, build_tool_catalog


def register_tools(
    mcp: FastMCP,
    notes: NotesService,
    projects: ProjectsService,
    *,
    write_service: NotesWriteService | None = None,
) -> None:
    """Zarejestruj narzędzia z katalogu na drzwiach MCP.

    ``write_service`` opcjonalne: gdy ``None``, katalog (a więc i drzwi) zawiera
    tylko narzędzia odczytu; gdy podane, dochodzi mutujące ``save_note``.
    """
    for spec in build_tool_catalog(notes, projects, write_service=write_service):
        mcp.add_tool(spec.fn)


def register_event_tools(mcp: FastMCP, events: EventService) -> None:
    """Zarejestruj KURSOROWY odczyt zdarzeń (``read_events_since``) na drzwiach MCP (A3, ADR 0040).

    Osobne od ``register_tools`` (4+1 ZAMROŻONE, Bramka 1) — to celowe, ADR-owane, ADDYTYWNE
    rozszerzenie powierzchni: sesja Claude Code, inaczej niż runtime agenta WorkMate, nie dostaje
    ``extra_catalog``, więc kursorowy odczyt musi wejść wprost na FastMCP. Read-only ⇒ bez bramki
    (ADR 0002/0006). Wchodzi tylko przy podłączonym moście — patrz
    ``_events_service_if_present``.
    """
    for spec in build_events_since_catalog(events):
        mcp.add_tool(spec.fn)
