"""Golden-test zamrożonej powierzchni narzędzi MCP (Bramka 1 / ADR 0008; A3 / ADR 0040).

Kontrakt 4+1 narzędzi (search_notes, get_note, list_projects, get_project_status, save_note)
musi pozostać ZAMROŻONY: nazwy, opisy i wygenerowane schematy parametrów identyczne z baseline.
ADR 0040 dokłada ADDYTYWNIE jedno narzędzie odczytu — ``read_events_since`` — WYŁĄCZNIE gdy
podłączony jest most zdarzeń (``events.db`` istnieje). Dlatego test steruje obecnością mostu przez
``WORKMATE_EVENTS_DB`` (deterministycznie, niezależnie od ambientowego ``~/.workmate/events.db``):

- z mostem → pełna powierzchnia = baseline (4+1 + read_events_since),
- bez mostu → dokładnie zamrożone 4+1 (bajt-w-bajt podzbiór baseline).

Gdy którykolwiek padnie — zamrożona powierzchnia się ruszyła; zatrzymaj się i sprawdź.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from workmate.adapters.outbound.sqlite_events import SqliteEventStore
from workmate.server import build_server

_BASELINE = Path(__file__).parent / "tool_surface_baseline.json"
_EVENT_TOOL = "read_events_since"


def _surface(mcp: Any) -> dict[str, Any]:
    return {
        tool.name: {"description": tool.description, "parameters": tool.parameters}
        for tool in mcp._tool_manager.list_tools()
    }


def test_mcp_tool_surface_matches_frozen_baseline(monkeypatch, tmp_path):
    # Most obecny (deterministycznie) → powierzchnia = pełny baseline (4+1 + read_events_since).
    db = tmp_path / "events.db"
    SqliteEventStore(str(db))  # utwórz plik, by narzędzie zdarzeń się zarejestrowało
    monkeypatch.setenv("WORKMATE_EVENTS_DB", str(db))

    baseline = json.loads(_BASELINE.read_text(encoding="utf-8"))
    assert _surface(build_server()) == baseline


def test_event_tool_absent_without_bridge(monkeypatch, tmp_path):
    # Bez mostu → drzwi wracają DOKŁADNIE do zamrożonych 4+1 (bajt-w-bajt podzbiór baseline).
    monkeypatch.setenv("WORKMATE_EVENTS_DB", str(tmp_path / "absent.db"))

    surface = _surface(build_server())
    assert _EVENT_TOOL not in surface

    baseline = json.loads(_BASELINE.read_text(encoding="utf-8"))
    frozen = {name: spec for name, spec in baseline.items() if name != _EVENT_TOOL}
    assert surface == frozen
