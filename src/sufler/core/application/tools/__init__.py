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

Pakiet, nie moduł: jeden moduł na katalog narzędzi, wspólny ``ToolSpec`` i kształt
odpowiedzi w ``spec``. Ten plik jest JEDYNYM wejściem — ``build_*_catalog`` importuj
stąd, nie z modułów wewnętrznych.

**Podział na moduły NIE pokrywa się z podziałem na powierzchnie.** Zamrożona powierzchnia
MCP rozkłada się na CZTERY moduły, bo ``adapters/inbound/mcp/tools.py`` woła trzy
rejestratory:

- ``mcp.build_tool_catalog`` — ``get_project_status`` i bramkowane ``save_note``; jest też
  routerem komend, więc woła go strona agenta (CLAUDE.md, reguła 6);
- ``notes_read.build_notes_read_catalog`` — ``search_notes``/``get_note``/``list_projects``,
  wsypywane do powyższego bajt w bajt;
- ``events.build_events_since_catalog`` — ``read_events_since``;
- ``jira.build_my_jira_tasks_catalog`` (SAM builder, nie cały moduł) —
  ``get_my_jira_tasks``/``get_my_jira_history``.

Komplet trzyma golden-test ``tests/adapters/test_mcp_tool_surface.py``. Reszta modułów jest
swobodna. „Leży obok modułów agenta" nie znaczy „jest po stronie agenta" — przed zmianą
w tych czterech miejscach sprawdź golden.
"""

from __future__ import annotations

from sufler.core.application.tools.activity import build_activity_catalog
from sufler.core.application.tools.events import _MAX_EVENTS_READ, build_events_since_catalog
from sufler.core.application.tools.file import build_file_catalog
from sufler.core.application.tools.file_reply import (
    build_file_reply_catalog,
    build_user_doc_push_catalog,
    build_user_image_push_catalog,
)
from sufler.core.application.tools.jira import (
    build_jira_catalog,
    build_my_jira_tasks_catalog,
)
from sufler.core.application.tools.mcp import build_tool_catalog
from sufler.core.application.tools.naming import przemianuj_na_konwencje_agenta
from sufler.core.application.tools.notes_read import (
    build_agent_notes_read_catalog,
    build_notes_read_catalog,
)
from sufler.core.application.tools.project import build_project_catalog
from sufler.core.application.tools.schedule import build_schedule_catalog
from sufler.core.application.tools.shell import build_shell_catalog
from sufler.core.application.tools.spec import ToolSpec
from sufler.core.application.tools.workspace import build_workspace_catalog

# ``_MAX_EVENTS_READ`` w re-eksporcie NIE jest przeoczeniem: sufit odczytu zdarzeń bierze stąd
# ``tests/core/test_events_since_tool.py``, żeby nie przepisywać liczby z kodu do testu.
__all__ = [
    "ToolSpec",
    "_MAX_EVENTS_READ",
    "build_activity_catalog",
    "build_agent_notes_read_catalog",
    "build_events_since_catalog",
    "build_file_catalog",
    "build_file_reply_catalog",
    "build_jira_catalog",
    "build_my_jira_tasks_catalog",
    "build_notes_read_catalog",
    "build_project_catalog",
    "build_schedule_catalog",
    "build_shell_catalog",
    "build_tool_catalog",
    "build_user_doc_push_catalog",
    "build_user_image_push_catalog",
    "build_workspace_catalog",
    "przemianuj_na_konwencje_agenta",
]
