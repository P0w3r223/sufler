"""Testy punktu składania serwera — profil drzwi sieciowych (Bramka 3 / ADR 0007).

Krytyczny invariant bezpieczeństwa: drzwi HTTP są tylko-do-odczytu KONSTRUKCYJNIE,
niezależnie od środowiska. Nawet gdy ``WORKMATE_ENABLE_WRITE`` jest włączone,
mutujące ``save_note`` nie może się pojawić na drzwiach sieciowych.
"""

from __future__ import annotations

from dataclasses import replace

from workmate.adapters.outbound.sqlite_events import SqliteEventStore
from workmate.config import Settings
from workmate.server import _build_http_server

_READ_TOOLS = {"search_notes", "get_note", "list_projects", "get_project_status"}
# Kursorowy odczyt EventStore (ADR 0040) — dokłada się na drzwiach HTTP, gdy most jest w użyciu.
_EVENT_TOOL = "read_events_since"


def test_http_server_is_read_only_even_when_enable_write_true():
    settings = replace(Settings.from_env(), enable_write=True)

    server = _build_http_server(settings)
    names = {t.name for t in server._tool_manager.list_tools()}

    assert "save_note" not in names  # zapis NIGDY nie wychodzi do sieci
    assert names >= _READ_TOOLS


def test_http_deployment_surface_is_reads_plus_event_cursor(monkeypatch, tmp_path):
    """T05b: realna powierzchnia drzwi HTTP na flocie = 4 odczyty + kursorowy odczyt EventStore.

    T05 (SMOKE_TEST) izoluje ``events.db`` (nieistniejąca ścieżka), by zamrozić deterministyczne
    4+1 — ale to zestaw drzwi STDIO (``save_note`` obecne, bo stdio ma ``enable_write=True``). Na
    FLOCIE ``events.db`` LEŻY na wolumenie ``state``, a drzwi HTTP wymuszają ``enable_write=False``
    KONSTRUKCYJNIE (ADR 0007), więc realny zestaw to 4 odczyty + ``read_events_since`` (ADR 0040),
    BEZ ``save_note``. Ten test enumeruje powierzchnię wdrożeniową (odpowiada T05b ze scenariusza).
    """
    db = tmp_path / "events.db"
    SqliteEventStore(str(db))  # utwórz plik — most obecny, narzędzie zdarzeń się rejestruje
    monkeypatch.setenv("WORKMATE_EVENTS_DB", str(db))

    # Profil wdrożeniowy: nawet z ``enable_write=True`` w env drzwi HTTP wycinają zapis (ADR 0007).
    server = _build_http_server(replace(Settings.from_env(), enable_write=True))
    names = {t.name for t in server._tool_manager.list_tools()}

    assert names == _READ_TOOLS | {_EVENT_TOOL}  # dokładnie 4 odczyty + kursorowy odczyt zdarzeń
    assert "save_note" not in names
