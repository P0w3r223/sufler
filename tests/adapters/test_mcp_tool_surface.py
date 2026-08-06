"""Golden-test zamrożonej powierzchni narzędzi MCP (Bramka 1 / ADR 0008; A3 / ADR 0040).

Kontrakt 4+1 narzędzi (search_notes, get_note, list_projects, get_project_status, save_note)
musi pozostać ZAMROŻONY: nazwy, opisy i wygenerowane schematy parametrów identyczne z baseline.
Dwie zdolności wchodzą ADDYTYWNIE, każda pod własnym warunkiem konfiguracji:

- ``read_events_since`` (ADR 0040) — gdy podłączony jest most zdarzeń (``events.db`` istnieje);
- ``get_my_jira_tasks`` i ``get_my_jira_history`` (ADR 0054) — gdy operator skonfigurował jedno
  stałe konto Jira (``WORKMATE_JIRA_MY_ACCOUNT``) obok URL-a i tokenu.

**Baseline obejmuje WSZYSTKIE osiem i test biega w czterech konfiguracjach — to jest poprawka,
nie kosmetyka.** Wcześniej baseline znał sześć nazw, a para Jiry trafiała na te same drzwi bez
żadnego zamrożenia schematu: w środowisku runnera zmiennej Jiry nie ma, więc golden przechodził,
a „zamrożona powierzchnia" opisywała konfigurację testu, nie produkcję. Powierzchnia mogła się
ruszyć w produkcji i żadna bramka by tego nie zobaczyła.

Gdy którykolwiek padnie — zamrożona powierzchnia się ruszyła; zatrzymaj się i sprawdź.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from workmate.adapters.outbound.sqlite_events import SqliteEventStore
from workmate.server import build_server

_BASELINE = Path(__file__).parent / "tool_surface_baseline.json"
_FROZEN = {"search_notes", "get_note", "list_projects", "get_project_status", "save_note"}
_EVENT_TOOL = "read_events_since"
_JIRA_TOOLS = {"get_my_jira_tasks", "get_my_jira_history"}


def _surface(mcp: Any) -> dict[str, Any]:
    return {
        tool.name: {"description": tool.description, "parameters": tool.parameters}
        for tool in mcp._tool_manager.list_tools()
    }


def _baseline() -> dict[str, Any]:
    return json.loads(_BASELINE.read_text(encoding="utf-8"))


def _configure(monkeypatch, tmp_path, *, bridge: bool, jira: bool) -> None:
    """Ustaw środowisko DETERMINISTYCZNIE — niezależnie od ambientowego `~/.workmate`."""
    db = tmp_path / "events.db"
    if bridge:
        SqliteEventStore(str(db))  # utwórz plik, by narzędzie zdarzeń się zarejestrowało
    monkeypatch.setenv("WORKMATE_EVENTS_DB", str(db if bridge else tmp_path / "absent.db"))
    # Baseline zamraża powierzchnię PRZY WŁĄCZONYM zapisie (save_note obecne); od amendmentu
    # ADR 0006 (2026-07-31) enable_write jest domyślnie OFF wszędzie, więc test musi go włączyć
    # jawnie — inaczej porównuje z baseline dziurę zamiast kontrakt.
    monkeypatch.setenv("WORKMATE_ENABLE_WRITE", "true")
    # Para Jiry jest bramkowana także transportem (`server.py`: znika na streamable-http, bo jeden
    # principal na proces nie obsłuży wielu osób). Bez przypięcia ambientowe
    # WORKMATE_TRANSPORT=streamable-http wywracałoby dwie konfiguracje — determinizm ma być pełny.
    monkeypatch.setenv("WORKMATE_TRANSPORT", "stdio")
    if jira:
        monkeypatch.setenv("WORKMATE_JIRA_BASE_URL", "https://jira.example.org")
        monkeypatch.setenv("WORKMATE_JIRA_TOKEN", "pat-secret")
        monkeypatch.setenv("WORKMATE_JIRA_MY_ACCOUNT", "mikolaj@example.org")
    else:
        monkeypatch.delenv("WORKMATE_JIRA_MY_ACCOUNT", raising=False)


@pytest.mark.parametrize(
    ("bridge", "jira", "expected"),
    [
        (True, True, _FROZEN | {_EVENT_TOOL} | _JIRA_TOOLS),
        (True, False, _FROZEN | {_EVENT_TOOL}),
        (False, True, _FROZEN | _JIRA_TOOLS),
        (False, False, _FROZEN),
    ],
    ids=["most+jira", "sam most", "sama jira", "goła powierzchnia"],
)
def test_surface_matches_baseline_in_every_configuration(
    monkeypatch, tmp_path, bridge: bool, jira: bool, expected: set[str]
):
    """Każda konfiguracja daje PODZBIÓR baseline, bajt w bajt — łącznie z opisami i schematami."""
    _configure(monkeypatch, tmp_path, bridge=bridge, jira=jira)

    surface = _surface(build_server())

    assert set(surface) == expected
    assert surface == {name: spec for name, spec in _baseline().items() if name in expected}


def test_baseline_holds_every_tool_the_surface_can_expose():
    """Baseline opisujący podzbiór realnej powierzchni jest gorszy niż brak baseline'u:
    wygląda jak bramka, a przepuszcza wszystko, czego nie wymienia."""
    assert set(_baseline()) == _FROZEN | {_EVENT_TOOL} | _JIRA_TOOLS
