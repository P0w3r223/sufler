"""Testy punktu składania serwera — profil drzwi sieciowych (Bramka 3 / ADR 0007).

Krytyczny invariant bezpieczeństwa: drzwi HTTP są tylko-do-odczytu KONSTRUKCYJNIE,
niezależnie od środowiska. Nawet gdy ``WORKMATE_ENABLE_WRITE`` jest włączone,
mutujące ``save_note`` nie może się pojawić na drzwiach sieciowych.
"""

from __future__ import annotations

from dataclasses import replace

from workmate.config import Settings
from workmate.server import _build_http_server

_READ_TOOLS = {"search_notes", "get_note", "list_projects", "get_project_status"}


def test_http_server_is_read_only_even_when_enable_write_true():
    settings = replace(Settings.from_env(), enable_write=True)

    server = _build_http_server(settings)
    names = {t.name for t in server._tool_manager.list_tools()}

    assert "save_note" not in names  # zapis NIGDY nie wychodzi do sieci
    assert names >= _READ_TOOLS
