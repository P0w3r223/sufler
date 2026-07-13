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
    telegram_command_names,
)
from workmate.adapters.outbound.sqlite_conversations import SqliteConversationStore
from workmate.core.application.conversations import ConversationService
from workmate.core.domain.pricing import TokenUsage

_CTX = CommandContext("telegram", "chat1")


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
) -> tuple[CommandRouter, _SpyTools]:
    tools = _SpyTools(canned or {})
    router = CommandRouter(service or _service(), tools.as_map())
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
    """``/nowa@WorkMateBot`` (grupy Telegrama) rozpoznaje się jako ``/nowa``."""
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


# --- telegram_command_names: guard driftu tokenów -------------------------------


def test_telegram_command_names_lists_all_tokens_without_slash():
    """Guard: nazwy dla PTB = wszystkie tokeny bez ukośnika (rejestr i handler zgodne)."""
    assert telegram_command_names() == [
        "pomoc",
        "help",
        "nowa",
        "nowy",
        "new",
        "szukaj",
        "projekty",
        "status",
        "historia",
    ]
