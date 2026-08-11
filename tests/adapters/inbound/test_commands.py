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
from workmate.core.errors import NoteAuthorizationError

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
    note_read_authorizer=None,
) -> tuple[CommandRouter, _SpyTools]:
    tools = _SpyTools(canned or {})
    router = CommandRouter(
        service or _service(),
        tools.as_map(),
        supports_attachments=supports_attachments,
        my_jira_tasks=my_jira_tasks,
        note_read_authorizer=note_read_authorizer,
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


# --- autoryzacja ODCZYTU w /szukaj i /projekty (ADR 0062) ------------------------


class _StubReadAuthz:
    """Atrapa authorizera odczytu: przepuszcza znane AAD id, resztę odrzuca (fail-closed)."""

    def __init__(self, allowed: set[str]) -> None:
        self._allowed = allowed

    def authorize(self, requester_aad_id: str) -> None:
        if requester_aad_id not in self._allowed:
            raise NoteAuthorizationError(
                "nadawca nie jest rozpoznanym członkiem pionu (stub, ADR 0062)"
            )


_HIT = {
    "search_notes": {
        "count": 1,
        "results": [
            {
                "date": "2025-06-12",
                "project": "mpwik",
                "title": "Przegląd API",
                "snippet": "fragment",
                "id": "a/b/c",
            }
        ],
    }
}
_PROJECTS = {"list_projects": {"count": 1, "projects": [{"key": "k", "name": "N", "company": ""}]}}


def test_search_denied_for_unrecognized_sender():
    # Nadawca spoza mapy → odmowa, narzędzie wyszukiwania NIE wołane (fail-closed).
    router, tools = _router(_HIT, note_read_authorizer=_StubReadAuthz(allowed=set()))
    out = router.dispatch("/szukaj scada", _CTX_WITH_SENDER)
    assert out is not None
    assert "Brak uprawnień do odczytu bazy wiedzy" in out
    assert tools.search_query is None


def test_search_allowed_for_recognized_member():
    # Rozpoznany członek → wyszukiwanie biegnie normalnie.
    router, tools = _router(_HIT, note_read_authorizer=_StubReadAuthz(allowed={"aad-123"}))
    out = router.dispatch("/szukaj scada", _CTX_WITH_SENDER)
    assert out is not None
    assert "Znaleziono 1" in out
    assert tools.search_query == "scada"


def test_projects_denied_for_unrecognized_sender():
    router, tools = _router(_PROJECTS, note_read_authorizer=_StubReadAuthz(allowed=set()))
    out = router.dispatch("/projekty", _CTX_WITH_SENDER)
    assert out is not None
    assert "Brak uprawnień do odczytu bazy wiedzy" in out
    assert tools.list_projects_called is False


def test_projects_allowed_for_recognized_member():
    router, tools = _router(_PROJECTS, note_read_authorizer=_StubReadAuthz(allowed={"aad-123"}))
    out = router.dispatch("/projekty", _CTX_WITH_SENDER)
    assert out is not None
    assert tools.list_projects_called is True


def test_search_without_authorizer_unchanged():
    # Bramka OFF (authorizer None) → zachowanie sprzed ADR 0062, mimo obecnego sender_id.
    router, tools = _router(_HIT)
    out = router.dispatch("/szukaj scada", _CTX_WITH_SENDER)
    assert out is not None
    assert "Znaleziono 1" in out
    assert tools.search_query == "scada"


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


def _jira_spec(tasks=None, history=None, error=None):
    """PRAWDZIWY ``build_jira_catalog`` — nie atrapa ``ToolSpec`` z wymyśloną nazwą i kształtem.

    Ta funkcja jest sednem tego bloku. Regresja z kroku 5.3 (nazwa, wymagana ``action``, klucze
    wyniku — trzy rzeczy naraz) przeszła przez komplet zielonych testów właśnie dlatego, że
    atrapy stały po OBU stronach szwu: router dopasowywał ``get_my_jira_tasks``, a test podawał
    mu ``ToolSpec`` o tej nazwie. Szew był niesprawdzony, choć obie jego strony miały pokrycie.
    """
    from workmate.core.application.tools import build_jira_catalog
    from workmate.core.domain.jira_tasks import JiraTask

    class _Mine:
        def my_open_tasks(self):
            if error:
                raise error
            return tasks or []

        def my_history(self, since="", until=""):
            return (history or [], False)

    class _Read:
        # Uwaga przy rozszerzaniu: to zwraca `[]` na KAŻDĄ metodę, a `member_history` ma
        # w kontrakcie krotkę `(zadania, truncated)`. Dla `/moje-zadania` nieosiągalne (router
        # woła wyłącznie `my_tasks`), ale sondy na akcje członka wymagają prawdziwej atrapy.
        def __getattr__(self, n):
            return lambda *a, **k: []

    return JiraTask, build_jira_catalog(_Mine(), _Read(), lambda n: None)[0]


def test_moje_zadania_wola_narzedzie_ktore_fabryka_naprawde_daje():
    """Sonda KONTRAKTOWA na szew router↔fabryka — jedyna, która łapie zmianę nazwy lub sygnatury.

    Router jest konsumentem NIE-modelowym: nie widzi go ani golden-test powierzchni MCP, ani
    żadna sonda na ``input_schema``. Reguła „sprawdź, czy builder nie ma drugiego konsumenta"
    (ADR 0009) dotyczy więc także kodu aplikacji, nie tylko drugich drzwi.
    """
    JiraTask, spec = _jira_spec()
    router, _ = _router(my_jira_tasks=lambda sender_id: [spec])
    out = router.dispatch("/moje-zadania", _CTX_WITH_SENDER)
    assert out == "Nie masz otwartych zadań w Jirze."


def test_moje_zadania_formatuje_zadania_przypisane():
    JiraTask, _ = _jira_spec()
    zadanie = JiraTask(
        key="WM-5",
        summary="Zrobić X",
        status="In Progress",
        priority="High",
        due_date="2026-08-01",
        assignee="Ja",
        url="https://jira.example.org/browse/WM-5",
    )
    _, spec = _jira_spec(tasks=[zadanie])
    router, _ = _router(my_jira_tasks=lambda sender_id: [spec])
    out = router.dispatch("/moje-zadania", _CTX_WITH_SENDER)
    assert out is not None
    assert "Twoje otwarte zadania (1):" in out
    assert "WM-5" in out and "Zrobić X" in out and "In Progress" in out
    assert "https://jira.example.org/browse/WM-5" in out


def test_moje_zadania_formatuje_obie_sekcje():
    JiraTask, _ = _jira_spec()
    przypisane = JiraTask(key="WM-5", summary="Zrobić X", status="In Progress", assignee="Ja")
    zgloszone = JiraTask(key="WM-9", summary="Zgłoszone", status="To Do")
    _, spec = _jira_spec(tasks=[przypisane, zgloszone])
    router, _ = _router(my_jira_tasks=lambda sender_id: [spec])
    out = router.dispatch("/moje-zadania", _CTX_WITH_SENDER)
    assert out is not None
    assert "Twoje otwarte zadania (1):" in out
    assert "Zgłoszone przez Ciebie, nieprzypisane do nikogo (1):" in out
    assert "WM-9" in out


def test_moje_zadania_bierze_narzedzie_po_nazwie_a_nie_po_pozycji():
    """Fabryka zwraca dziś jedno narzędzie, ale kolejność nie może być kontraktem."""
    _, spec = _jira_spec()

    def nie_wolac(**kw):
        raise AssertionError("nie powinno być wołane przez /moje-zadania")

    router, _ = _router(my_jira_tasks=lambda sender_id: [ToolSpec("Schedule", "", nie_wolac), spec])
    assert router.dispatch("/moje-zadania", _CTX_WITH_SENDER) == "Nie masz otwartych zadań w Jirze."


def test_moje_zadania_bez_narzedzia_jiry_degraduje_do_odmowy():
    router, _ = _router(my_jira_tasks=lambda sender_id: [ToolSpec("Schedule", "", lambda: {})])
    out = router.dispatch("/moje-zadania", _CTX_WITH_SENDER)
    assert out == "Ta komenda nie jest skonfigurowana na tych drzwiach."


def test_moje_zadania_pokazuje_blad_narzedzia():
    from workmate.core.errors import JiraReadError

    _, spec = _jira_spec(error=JiraReadError("brak dostępu do Jiry"))
    router, _ = _router(my_jira_tasks=lambda sender_id: [spec])
    out = router.dispatch("/moje-zadania", _CTX_WITH_SENDER)
    assert out is not None and out.startswith("Błąd: ")


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
