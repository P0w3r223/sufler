"""Testy dispatchera komend read-only (``CommandRouter``, styl Claude Code).

Bez sieci i bez LLM: atrapa katalogu narzędzi ODCZYTU (callable → kanned dict) +
prawdziwy ``ConversationService`` nad ``SqliteConversationStore(":memory:")``. Sprawdzamy
parser ``dispatch`` (pierwszy token, sufiks ``@bot``, lowercase, zachowane argumenty), każdą
komendę oraz strukturalną gwarancję read-only (router nie widzi ``save_note``).
"""

from __future__ import annotations

from workmate.adapters.inbound.commands import (
    _NEW_THREAD_ACK,
    _NEW_THREAD_ALREADY_FRESH,
    COMMAND_SPECS,
    CommandContext,
    CommandRouter,
)
from workmate.adapters.outbound.sqlite_conversations import SqliteConversationStore
from workmate.core.application.conversations import ConversationService
from workmate.core.application.tools import ToolSpec
from workmate.core.domain.pricing import TokenUsage

_CTX = CommandContext("telegram", "chat1")
_CTX_WITH_SENDER = CommandContext("teams_graph", "chat1", "aad-123")


class _SpyTools:
    """Atrapa katalogu narzędzi odczytu: notuje wywołania i zwraca kanned dict per nazwa."""

    def __init__(self, canned: dict[str, dict[str, object]]) -> None:
        self._canned = canned
        self.search_query: str | None = None
        self.status_project: str | None = None
        self.list_projects_called = False

    def as_map(self) -> dict[str, object]:
        return {
            "search_notes": self._search,
            "list_projects": self._list,
            "get_project_status": self._status,
        }

    def _search(self, *, query: str) -> dict[str, object]:
        self.search_query = query
        return self._canned["search_notes"]

    def _list(self) -> dict[str, object]:
        self.list_projects_called = True
        return self._canned["list_projects"]

    def _status(self, *, project: str) -> dict[str, object]:
        self.status_project = project
        return self._canned["get_project_status"]


def _service() -> ConversationService:
    store = SqliteConversationStore(":memory:")
    return ConversationService(store, max_context_tokens=1000)


def _router(
    canned: dict[str, dict[str, object]] | None = None,
    service: ConversationService | None = None,
    *,
    supports_attachments: bool = False,
    my_jira_tasks=None,
) -> tuple[CommandRouter, _SpyTools]:
    tools = _SpyTools(canned or {})
    router = CommandRouter(
        service or _service(),
        tools.as_map(),
        supports_attachments=supports_attachments,
        my_jira_tasks=my_jira_tasks,
    )
    return router, tools


# --- Parser: dispatch (pierwszy token, sufiks @bot, lowercase, argumenty) --------


def test_dispatch_unknown_slash_returns_none():
    router, _ = _router()
    assert router.dispatch("/foo", _CTX) is None


def test_dispatch_non_slash_text_returns_none():
    """Zwykła wiadomość (bez ukośnika) → ``None`` (idzie do normalnej tury, bez porywania)."""
    router, _ = _router()
    assert router.dispatch("dzień dobry", _CTX) is None


def test_dispatch_empty_and_whitespace_return_none():
    router, _ = _router()
    assert router.dispatch("", _CTX) is None
    assert router.dispatch("   \n\t ", _CTX) is None


def test_dispatch_strips_bot_suffix_from_first_token():
    """``/nowa@WorkMateBot`` (konwencja komend grupowych) rozpoznaje się jako ``/nowa``."""
    router, _ = _router()
    # Świeży wątek → komenda /nowa odpowiada „już pusta rozmowa" (dowód, że trafił handler).
    assert router.dispatch("/nowa@WorkMateBot", _CTX) == _NEW_THREAD_ALREADY_FRESH


def test_dispatch_is_case_insensitive_on_token():
    router, _ = _router()
    assert router.dispatch("/NOWA", _CTX) == _NEW_THREAD_ALREADY_FRESH


def test_dispatch_preserves_multiword_arguments_for_search():
    """``/szukaj foo bar`` → argument ``foo bar`` (z wieloma słowami) trafia do search_notes."""
    router, tools = _router({"search_notes": {"results": []}})
    router.dispatch("/szukaj foo bar", _CTX)
    assert tools.search_query == "foo bar"


# --- /pomoc ---------------------------------------------------------------------


def test_help_lists_every_command_token():
    """``/pomoc`` wypisuje WSZYSTKIE tokeny z rejestru (guard: nic nie wypadło z pomocy)."""
    router, _ = _router()
    out = router.dispatch("/pomoc", _CTX)
    assert out is not None
    for spec in COMMAND_SPECS:
        for token in spec.tokens:
            assert token in out


def test_help_alias_works():
    router, _ = _router()
    assert router.dispatch("/help", _CTX) == router.dispatch("/pomoc", _CTX)


def test_help_includes_intro_and_examples():
    """``/pomoc`` prowadzi za rękę: krótkie intro + sekcja przykładowych pytań (F7)."""
    router, _ = _router()
    out = router.dispatch("/pomoc", _CTX)
    assert out is not None
    assert "WorkMate" in out
    assert "Przykłady pytań:" in out
    assert out.count("•") >= 3


def test_help_surfaces_attachment_capability_only_when_supported():
    """``/pomoc`` uwidacznia multimodal (F8) tylko na drzwiach z plikami, nie globalnie."""
    on, _ = _router(supports_attachments=True)
    out_on = on.dispatch("/pomoc", _CTX)
    assert out_on is not None
    lowered = out_on.lower()
    assert "wrzuć" in lowered
    assert "hmi" in lowered or "pdf" in lowered

    off, _ = _router(supports_attachments=False)
    out_off = off.dispatch("/pomoc", _CTX)
    assert out_off is not None
    assert "wrzuć" not in out_off.lower()  # drzwi tekstowe nie obiecują załączników


# --- /nowa (start_new_thread) ---------------------------------------------------


def test_new_thread_on_fresh_conversation_reports_already_fresh():
    router, _ = _router()
    assert router.dispatch("/nowa", _CTX) == _NEW_THREAD_ALREADY_FRESH


def test_new_thread_acks_when_thread_had_turns():
    """Po realnej turze ``/nowa`` domyka wątek i potwierdza (ACK)."""
    service = _service()
    store = service._store
    conv = store.open_conversation("telegram", "chat1")
    store.append_message(conv.id, "user", "pierwsza")
    store.append_message(conv.id, "assistant", "odp")
    router, _ = _router(service=service)

    assert router.dispatch("/nowa", _CTX) == _NEW_THREAD_ACK
    # Wątek faktycznie domknięty — brak aktywnej rozmowy.
    assert store.active_conversation("telegram", "chat1") is None


# --- /szukaj --------------------------------------------------------------------


def test_search_formats_hits():
    router, _ = _router(
        {
            "search_notes": {
                "count": 1,
                "results": [
                    {
                        "date": "2025-06-12",
                        "project": "mpwik",
                        "title": "Przegląd API",
                        "snippet": "fragment o SCADA",
                        "id": "mpwik/scada/2025-06-12-api",
                    }
                ],
            }
        }
    )
    out = router.dispatch("/szukaj scada", _CTX)
    assert out is not None
    assert "Znaleziono 1" in out
    assert "2025-06-12" in out
    assert "[mpwik]" in out
    assert "Przegląd API" in out
    assert "fragment o SCADA" in out
    assert "id: mpwik/scada/2025-06-12-api" in out


def test_search_empty_argument_shows_usage_hint_without_calling_tool():
    router, tools = _router({"search_notes": {"results": []}})
    out = router.dispatch("/szukaj", _CTX)
    assert out is not None
    assert out.startswith("Użycie: /szukaj")
    assert tools.search_query is None  # narzędzie NIE wołane dla pustego zapytania


def test_search_empty_results_reports_no_matches():
    router, _ = _router({"search_notes": {"count": 0, "results": []}})
    assert router.dispatch("/szukaj nic", _CTX) == "Brak notatek pasujących do zapytania."


def test_search_error_branch_surfaces_error_message():
    router, _ = _router({"search_notes": {"error": "FTS niedostępne"}})
    out = router.dispatch("/szukaj x", _CTX)
    assert out is not None
    assert "Błąd wyszukiwania: FTS niedostępne" in out


# --- /projekty ------------------------------------------------------------------


def test_projects_formats_registry_entries():
    router, tools = _router(
        {
            "list_projects": {
                "count": 2,
                "projects": [
                    {"key": "workmate", "name": "WorkMate", "company": "biap"},
                    {"key": "scada", "name": "SCADA", "company": ""},
                ],
            }
        }
    )
    out = router.dispatch("/projekty", _CTX)
    assert out is not None
    assert tools.list_projects_called
    assert "• workmate (biap) — WorkMate" in out
    # Bez firmy nawias się nie pojawia.
    assert "• scada — SCADA" in out


def test_projects_empty_registry():
    router, _ = _router({"list_projects": {"count": 0, "projects": []}})
    assert router.dispatch("/projekty", _CTX) == "Brak projektów w rejestrze."


# --- /status --------------------------------------------------------------------


def test_status_with_argument_formats_project_status():
    router, tools = _router(
        {
            "get_project_status": {
                "key": "workmate",
                "name": "WorkMate",
                "status": "active",
                "health": "green",
                "phase": "Faza 2",
                "summary": "Prace w toku",
                "notes_count": 3,
                "open_action_items": 5,
            }
        }
    )
    out = router.dispatch("/status workmate", _CTX)
    assert out is not None
    assert tools.status_project == "workmate"
    assert "[workmate] WorkMate — active / green / faza: Faza 2" in out
    assert "Prace w toku" in out
    assert "Notatki: 3" in out
    assert "otwarte action items: 5" in out


def test_status_without_argument_on_no_thread_reports_no_active():
    router, _ = _router()
    out = router.dispatch("/status", _CTX)
    assert out is not None
    assert out.startswith("Brak aktywnego wątku")


def test_status_without_argument_after_real_turn_reports_thread_state():
    """Bez argumentu ``/status`` opisuje BIEŻĄCY wątek (liczba tur > 0)."""
    service = _service()
    store = service._store
    conv = store.open_conversation("telegram", "chat1")
    store.append_message(conv.id, "user", "pierwsza")
    store.append_message(conv.id, "assistant", "odp")
    router, _ = _router(service=service)

    out = router.dispatch("/status", _CTX)
    assert out is not None
    assert "Bieżący wątek: 2 tur." in out


# --- /historia ------------------------------------------------------------------


def test_history_empty_reports_no_conversations():
    router, _ = _router()
    assert router.dispatch("/historia", _CTX) == "Brak zapisanych rozmów."


def test_history_lists_recent_conversations():
    service = _service()
    store = service._store
    conv = store.open_conversation("telegram", "chat1")
    store.append_message(conv.id, "user", "q")
    store.append_message(
        conv.id, "assistant", "a", usage=TokenUsage(input_tokens=10, output_tokens=2)
    )
    router, _ = _router(service=service)

    out = router.dispatch("/historia", _CTX)
    assert out is not None
    assert out.startswith("Ostatnie rozmowy:")
    assert "2 tur" in out
    assert "12 tok" in out  # realne usage rozmowy (10 + 2)


# --- /moje-zadania (ADR 0054) ----------------------------------------------------


def test_my_tasks_without_factory_reports_not_configured():
    router, _ = _router()
    out = router.dispatch("/moje-zadania", _CTX_WITH_SENDER)
    assert out == "Ta komenda nie jest skonfigurowana na tych drzwiach."


def test_my_tasks_alias_works():
    router, _ = _router()
    assert router.dispatch("/zadania", _CTX_WITH_SENDER) == router.dispatch(
        "/moje-zadania", _CTX_WITH_SENDER
    )


def test_my_tasks_unresolved_identity_reports_fail_closed_denial():
    """Fabryka zwraca pustą listę (brak mapowania sender_id → konto Jira) — fail-closed."""

    def factory(sender_id: str) -> list[ToolSpec]:
        assert sender_id == "aad-123"
        return []

    router, _ = _router(my_jira_tasks=factory)
    out = router.dispatch("/moje-zadania", _CTX_WITH_SENDER)
    assert out is not None
    assert "nie udało się ustalić" in out.lower()


def test_my_tasks_formats_assigned_tasks():
    def get_my_jira_tasks() -> dict[str, object]:
        return {
            "assigned_to_me": [
                {
                    "key": "WM-5",
                    "summary": "Zrobić X",
                    "status": "In Progress",
                    "priority": "High",
                    "due_date": "2026-08-01",
                    "url": "https://jira.example.org/browse/WM-5",
                }
            ],
            "reported_by_me_unassigned": [],
        }

    def factory(sender_id: str) -> list[ToolSpec]:
        return [ToolSpec("get_my_jira_tasks", "", get_my_jira_tasks)]

    router, _ = _router(my_jira_tasks=factory)
    out = router.dispatch("/moje-zadania", _CTX_WITH_SENDER)
    assert out is not None
    assert "WM-5" in out
    assert "Zrobić X" in out
    assert "In Progress" in out
    assert "https://jira.example.org/browse/WM-5" in out
    assert "Twoje otwarte zadania (1):" in out


def test_my_tasks_formats_both_sections():
    def get_my_jira_tasks() -> dict[str, object]:
        return {
            "assigned_to_me": [{"key": "WM-5", "summary": "Zrobić X", "status": "In Progress"}],
            "reported_by_me_unassigned": [
                {"key": "WM-9", "summary": "Zgłoszone", "status": "To Do"}
            ],
        }

    def factory(sender_id: str) -> list[ToolSpec]:
        return [ToolSpec("get_my_jira_tasks", "", get_my_jira_tasks)]

    router, _ = _router(my_jira_tasks=factory)
    out = router.dispatch("/moje-zadania", _CTX_WITH_SENDER)
    assert out is not None
    assert "Twoje otwarte zadania (1):" in out
    assert "Zgłoszone przez Ciebie, nieprzypisane do nikogo (1):" in out
    assert "WM-9" in out


def test_my_tasks_selects_tool_by_name_when_factory_returns_several():
    """Fabryka zwraca WIĘCEJ niż jedno narzędzie (ADR 0059) — router bierze po nazwie, nie
    pozycji."""

    def get_jira_task(key: str) -> dict[str, object]:
        raise AssertionError("nie powinno być wołane przez /moje-zadania")

    def get_my_jira_tasks() -> dict[str, object]:
        return {"assigned_to_me": [], "reported_by_me_unassigned": []}

    def factory(sender_id: str) -> list[ToolSpec]:
        return [
            ToolSpec("get_jira_task", "", get_jira_task),
            ToolSpec("get_my_jira_tasks", "", get_my_jira_tasks),
        ]

    router, _ = _router(my_jira_tasks=factory)
    out = router.dispatch("/moje-zadania", _CTX_WITH_SENDER)
    assert out == "Nie masz otwartych zadań w Jirze."


def test_my_tasks_empty_lists_report_no_open_tasks():
    def factory(sender_id: str) -> list[ToolSpec]:
        return [
            ToolSpec(
                "get_my_jira_tasks",
                "",
                lambda: {"assigned_to_me": [], "reported_by_me_unassigned": []},
            )
        ]

    router, _ = _router(my_jira_tasks=factory)
    out = router.dispatch("/moje-zadania", _CTX_WITH_SENDER)
    assert out == "Nie masz otwartych zadań w Jirze."


def test_my_tasks_missing_get_my_jira_tasks_tool_reports_not_configured():
    """Fabryka nie zawiera ``get_my_jira_tasks`` po nazwie — degradacja do czytelnej odmowy."""

    def factory(sender_id: str) -> list[ToolSpec]:
        return [ToolSpec("get_jira_task", "", lambda key: {})]

    router, _ = _router(my_jira_tasks=factory)
    out = router.dispatch("/moje-zadania", _CTX_WITH_SENDER)
    assert out == "Ta komenda nie jest skonfigurowana na tych drzwiach."


def test_my_tasks_surfaces_tool_error():
    def factory(sender_id: str) -> list[ToolSpec]:
        return [ToolSpec("get_my_jira_tasks", "", lambda: {"error": "brak dostępu do Jiry"})]

    router, _ = _router(my_jira_tasks=factory)
    out = router.dispatch("/moje-zadania", _CTX_WITH_SENDER)
    assert out == "Błąd: brak dostępu do Jiry"


# --- Read-only: router nie widzi save_note (bramka ADR 0006) --------------------


def test_router_works_with_readonly_map_lacking_save_note():
    """Router zbudowany na mapie BEZ ``save_note`` obsługuje komendy — komendy nie mutują."""
    read_only_map = {
        "search_notes": lambda *, query: {"results": []},
        "list_projects": lambda: {"projects": []},
        "get_project_status": lambda *, project: {"error": "brak"},
    }
    assert "save_note" not in read_only_map
    router = CommandRouter(_service(), read_only_map)

    assert router.dispatch("/szukaj cokolwiek", _CTX) == "Brak notatek pasujących do zapytania."
    assert router.dispatch("/projekty", _CTX) == "Brak projektów w rejestrze."
