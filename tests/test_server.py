"""Testy punktu składania serwera — profil drzwi sieciowych (Bramka 3 / ADR 0007).

Krytyczny invariant bezpieczeństwa: drzwi HTTP są tylko-do-odczytu KONSTRUKCYJNIE,
niezależnie od środowiska. Nawet gdy ``WORKMATE_ENABLE_WRITE`` jest włączone,
mutujące ``save_note`` nie może się pojawić na drzwiach sieciowych.

``bez_mostu`` wskazuje ``WORKMATE_EVENTS_DB`` na NIEISTNIEJĄCY plik. Bez tego powierzchnia
zależała od tego, czy deweloper ma u siebie ``~/.workmate/events.db`` (a ma, jeśli kiedykolwiek
uruchomił most): na jego maszynie testy oglądały pięć narzędzi, na świeżym runnerze cztery.
Asercje ``not in``/``>=`` przechodziły w obu przypadkach — czyli sonda milczała o tym, że mierzy
co innego niż myśli, i nie mogła zauważyć narzędzia, które wyciekło na drzwi sieciowe.

Ten fixture ZOSTAJE po uleniwieniu obiektu modułowego (przegląd kompozycji 2026-08-17): nie był
obejściem na efekt uboczny importu, tylko na REALNY plik w katalogu domowym dewelopera. Globalny
fixture z ``conftest.py`` zdejmuje ``WORKMATE_*``, więc bez ``bez_mostu``
``EventsSettings.from_env()`` wraca do domyślnego ``~/.workmate/events.db`` — który u dewelopera
istnieje, a na runnerze nie. Import modułu ma osobną sondę niżej.
"""

from __future__ import annotations

import importlib
from dataclasses import replace

import pytest

import workmate.server as server_module
from workmate.adapters.outbound.sqlite_events import SqliteEventStore
from workmate.config import Settings
from workmate.server import _build_http_server, build_server

_READ_TOOLS = {"search_notes", "get_note", "list_projects", "get_project_status"}
# Kursorowy odczyt EventStore (ADR 0040) — dokłada się na drzwiach HTTP, gdy most jest w użyciu.
_EVENT_TOOL = "read_events_since"
# "Moje zadania" Jira (ADR 0054) — addytywne, tylko gdy operator skonfigurował stały principal.
_MY_JIRA_TASKS_TOOL = "get_my_jira_tasks"


@pytest.fixture
def bez_mostu(monkeypatch, tmp_path):
    """Most zdarzeń NIEOBECNY — deterministycznie, niezależnie od ``~/.workmate`` operatora."""
    monkeypatch.setenv("WORKMATE_EVENTS_DB", str(tmp_path / "nie-ma-events.db"))


def test_my_jira_tasks_absent_without_configured_account(bez_mostu):
    server = build_server(Settings.from_env())
    names = {t.name for t in server._tool_manager.list_tools()}
    assert _MY_JIRA_TASKS_TOOL not in names


def test_my_jira_tasks_present_when_account_and_read_configured(monkeypatch, bez_mostu):
    monkeypatch.setenv("WORKMATE_JIRA_BASE_URL", "https://jira.example.org")
    monkeypatch.setenv("WORKMATE_JIRA_TOKEN", "pat-secret")
    monkeypatch.setenv("WORKMATE_JIRA_MY_ACCOUNT", "mikolaj@example.org")
    server = build_server(Settings.from_env())
    names = {t.name for t in server._tool_manager.list_tools()}
    assert _MY_JIRA_TASKS_TOOL in names


def test_my_jira_tasks_absent_when_account_set_but_base_url_missing(monkeypatch, bez_mostu):
    """Konto bez URL-a/tokenu Jiry to niekompletny cel — narzędzie NIE wchodzi (fail-quiet, nie
    fail-fast: to zdolność addytywna serwera MCP, jak kursor zdarzeń)."""
    monkeypatch.setenv("WORKMATE_JIRA_MY_ACCOUNT", "mikolaj@example.org")
    server = build_server(Settings.from_env())
    names = {t.name for t in server._tool_manager.list_tools()}
    assert _MY_JIRA_TASKS_TOOL not in names


def test_my_jira_tasks_absent_from_http_even_when_account_configured(monkeypatch, bez_mostu):
    """KONSTRUKCYJNE (jak `save_note`): jeden principal na proces nie może obsłużyć wielu osób
    na współdzielonym HTTP — narzędzie znika niezależnie od `WORKMATE_JIRA_MY_ACCOUNT` w env."""
    monkeypatch.setenv("WORKMATE_JIRA_BASE_URL", "https://jira.example.org")
    monkeypatch.setenv("WORKMATE_JIRA_TOKEN", "pat-secret")
    monkeypatch.setenv("WORKMATE_JIRA_MY_ACCOUNT", "mikolaj@example.org")

    server = _build_http_server(replace(Settings.from_env(), enable_write=True))
    names = {t.name for t in server._tool_manager.list_tools()}

    assert _MY_JIRA_TASKS_TOOL not in names


def test_http_server_is_read_only_even_when_enable_write_true(bez_mostu):
    """Bez mostu powierzchnia sieciowa to DOKŁADNIE cztery odczyty — nic więcej nie ma prawa wejść.

    Porównanie równością (a nie ``>=``) jest tu istotą sondy: nowe narzędzie zarejestrowane
    bezwarunkowo w ``build_server`` wychodziłoby na drzwi sieciowe i żaden test tego nie widział.
    """
    settings = replace(Settings.from_env(), enable_write=True)

    server = _build_http_server(settings)
    names = {t.name for t in server._tool_manager.list_tools()}

    assert "save_note" not in names  # zapis NIGDY nie wychodzi do sieci
    assert names == _READ_TOOLS


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


# --- import composition rootu jest bez efektów ubocznych ---------------------


@pytest.fixture
def swiezy_import():
    """Przeładowuje ``workmate.server`` i przywraca czysty stan po teście.

    Testy w tym pliku trzymają referencje do ``build_server``/``_build_http_server`` sprzed
    przeładowania — to w porządku, bo funkcje są bezstanowe. Przywracamy jednak moduł na czysto,
    żeby ewentualny zbudowany leniwie serwer nie wyciekł do kolejnych testów.
    """
    yield importlib.reload(server_module)
    importlib.reload(server_module)


def test_import_serwera_nie_czyta_srodowiska(monkeypatch, swiezy_import):
    """Import modułu NIE MOŻE budować serwera — ``mcp = build_server()`` robił to na kolekcji.

    Zła wartość ``WORKMATE_TRANSPORT`` jest tu sondą, bo ``Settings.from_env()`` rzuca na niej
    ``ValueError``: dopóki obiekt powstawał w treści modułu, sam import (a więc CAŁA kolekcja
    pytest, jeszcze przed fixturem czyszczącym ``WORKMATE_*``) wywracał się na konfiguracji
    maszyny. Przy okazji import przestaje budować lematyzator, sondować ``events.db``
    i alokować ``httpx.Client`` z ``atexit`` przy skonfigurowanej Jirze.
    """
    monkeypatch.setenv("WORKMATE_TRANSPORT", "nieznany-transport")

    przeladowany = importlib.reload(server_module)

    assert przeladowany.build_server is not None  # import przeszedł, mimo złego env


def test_atrybut_mcp_powstaje_leniwie_i_jest_ten_sam(bez_mostu, swiezy_import):
    """CLI FastMCP (``mcp dev``) szuka nazwy ``mcp`` przez ``hasattr``/``getattr``.

    PEP 562 (``__getattr__`` modułu) obsługuje oba wywołania, więc uleniwienie jest niewidoczne
    dla CLI. Sprawdzamy też, że kolejne sięgnięcia dają TEN SAM obiekt: ``mcp dev`` odpytuje
    moduł raz, ale ``hasattr`` + ``getattr`` to już dwa wywołania i budowa serwera per sięgnięcie
    byłaby cichą regresją kosztu.
    """
    assert hasattr(swiezy_import, "mcp")
    pierwszy = swiezy_import.mcp

    assert {t.name for t in pierwszy._tool_manager.list_tools()} >= _READ_TOOLS
    assert swiezy_import.mcp is pierwszy


def test_nieznany_atrybut_modulu_dalej_jest_bledem(swiezy_import):
    """``__getattr__`` modułu nie może zamienić literówki w import w cichy ``None``."""
    nazwa = "nie_ma_takiego_atrybutu"
    with pytest.raises(AttributeError):
        getattr(swiezy_import, nazwa)
