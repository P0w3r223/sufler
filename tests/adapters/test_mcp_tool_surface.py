"""Golden-test zamrożonej powierzchni narzędzi MCP (Bramka 1 / ADR 0008).

Przebudowa ``tools.py`` na jednoźródłowy katalog NIE może zmienić kontraktu 4+1
narzędzi: nazwy, opisy i wygenerowane schematy parametrów muszą pozostać
identyczne. Baseline (``tool_surface_baseline.json``) został zrzucony z FastMCP
PRZED przebudową; ten test asertuje równość po przebudowie. Jeśli kiedyś padnie,
to sygnał, że zamrożona powierzchnia się ruszyła — zatrzymaj się i sprawdź.
"""
from __future__ import annotations

import json
from pathlib import Path

from workmate.server import build_server

_BASELINE = Path(__file__).parent / "tool_surface_baseline.json"


def test_mcp_tool_surface_matches_frozen_baseline():
    baseline = json.loads(_BASELINE.read_text(encoding="utf-8"))

    mcp = build_server()
    surface = {
        tool.name: {"description": tool.description, "parameters": tool.parameters}
        for tool in mcp._tool_manager.list_tools()
    }

    assert surface == baseline
