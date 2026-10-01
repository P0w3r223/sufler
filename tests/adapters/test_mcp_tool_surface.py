"""Golden-test zamrożonej powierzchni narzędzi MCP (Bramka 1 / ADR 0008; A3 / ADR 0040).

Kontrakt 4+1 narzędzi (search_notes, get_note, list_projects, get_project_status, save_note)
musi pozostać ZAMROŻONY: nazwy, opisy i wygenerowane schematy parametrów identyczne z baseline.
Dwie zdolności wchodzą ADDYTYWNIE, każda pod własnym warunkiem konfiguracji:

- ``read_events_since`` (ADR 0040) — gdy podłączony jest most zdarzeń (``events.db`` istnieje);
- ``get_my_jira_tasks`` i ``get_my_jira_history`` (ADR 0054) — gdy operator skonfigurował jedno
  stałe konto Jira (``SUFLER_JIRA_MY_ACCOUNT``) obok URL-a i tokenu.

**Baseline obejmuje WSZYSTKIE osiem i test biega w czterech konfiguracjach — to jest poprawka,
nie kosmetyka.** Wcześniej baseline znał sześć nazw, a para Jiry trafiała na te same drzwi bez
żadnego zamrożenia schematu: w środowisku runnera zmiennej Jiry nie ma, więc golden przechodził,
a „zamrożona powierzchnia" opisywała konfigurację testu, nie produkcję. Powierzchnia mogła się
ruszyć w produkcji i żadna bramka by tego nie zobaczyła.

Gdy którykolwiek padnie — zamrożona powierzchnia się ruszyła; zatrzymaj się i sprawdź.
"""

from __future__ import annotations

import inspect
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from sufler.adapters.inbound.mcp import tools as mcp_tools
from sufler.adapters.outbound.sqlite_events import SqliteEventStore
from sufler.core.domain.models import NoteMetadata
from sufler.server import build_server

_BASELINE = Path(__file__).parent / "tool_surface_baseline.json"
_FROZEN = {"search_notes", "get_note", "list_projects", "get_project_status", "save_note"}
_EVENT_TOOL = "read_events_since"
_JIRA_TOOLS = {"get_my_jira_tasks", "get_my_jira_history"}

# Rejestratory pokryte macierzą konfiguracji niżej. Rejestrator ≠ narzędzie:
# `register_my_jira_tasks_tool` wystawia DWA. Dlatego ta lista pilnuje ŹRÓDEŁ narzędzi,
# a nie ich liczby — nowe źródło ma zerwać bramkę, zanim wejdzie niezauważone.
_COVERED_BY_MATRIX = {"register_tools", "register_event_tools", "register_my_jira_tasks_tool"}


def _surface(mcp: Any) -> dict[str, Any]:
    return {
        tool.name: {"description": tool.description, "parameters": tool.parameters}
        for tool in mcp._tool_manager.list_tools()
    }


def _baseline() -> dict[str, Any]:
    return json.loads(_BASELINE.read_text(encoding="utf-8"))


def _configure(monkeypatch, tmp_path, *, bridge: bool, jira: bool, write: bool = True) -> None:
    """Ustaw środowisko DETERMINISTYCZNIE — niezależnie od ambientowego `~/.sufler`."""
    db = tmp_path / "events.db"
    if bridge:
        SqliteEventStore(str(db))  # utwórz plik, by narzędzie zdarzeń się zarejestrowało
    monkeypatch.setenv("SUFLER_EVENTS_DB", str(db if bridge else tmp_path / "absent.db"))
    # Baseline zamraża powierzchnię PRZY WŁĄCZONYM zapisie (save_note obecne); od amendmentu
    # ADR 0006 (2026-07-31) enable_write jest domyślnie OFF wszędzie, więc test musi go włączyć
    # jawnie — inaczej porównuje z baseline dziurę zamiast kontrakt.
    monkeypatch.setenv("SUFLER_ENABLE_WRITE", "true" if write else "false")
    # Para Jiry jest bramkowana także transportem (`server.py`: znika na streamable-http, bo jeden
    # principal na proces nie obsłuży wielu osób). Bez przypięcia ambientowe
    # SUFLER_TRANSPORT=streamable-http wywracałoby dwie konfiguracje — determinizm ma być pełny.
    monkeypatch.setenv("SUFLER_TRANSPORT", "stdio")
    if jira:
        monkeypatch.setenv("SUFLER_JIRA_BASE_URL", "https://jira.example.org")
        monkeypatch.setenv("SUFLER_JIRA_TOKEN", "pat-secret")
        monkeypatch.setenv("SUFLER_JIRA_MY_ACCOUNT", "mikolaj@example.org")
    else:
        monkeypatch.delenv("SUFLER_JIRA_MY_ACCOUNT", raising=False)


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


def test_default_surface_without_write_is_frozen_too(monkeypatch, tmp_path):
    """DOMYŚLNA powierzchnia produkcyjna — bez zapisu — też musi być zamrożona.

    Cała macierz wyżej wymusza ``SUFLER_ENABLE_WRITE=true``, a od amendmentu ADR 0006 zapis
    jest domyślnie WYŁĄCZONY WSZĘDZIE. Zamrożony jest więc wariant, którego domyślnie nikt nie
    dostaje, a wariant, który dostają wszyscy, nie był porównywany z baseline w ogóle: regres
    w parsowaniu ``enable_write`` (albo w warunku rejestracji) zmieniłby realne drzwi bez
    zerwania żadnej bramki. Cztery odczyty muszą tu wyjść bajt w bajt jak w baseline — nie tylko
    „bez save_note".
    """
    _configure(monkeypatch, tmp_path, bridge=False, jira=False, write=False)

    surface = _surface(build_server())

    assert set(surface) == _FROZEN - {"save_note"}
    assert surface == {name: spec for name, spec in _baseline().items() if name in surface}


def test_write_gate_is_the_only_difference_between_the_two_default_profiles(monkeypatch, tmp_path):
    """Włączenie zapisu ma DOKŁADAĆ ``save_note`` i nic poza tym.

    Bramka zapisu przechodzi przez ``build_server`` (``write_service=None``), a nie przez
    filtrowanie gotowej listy — regres, w którym gałąź „bez zapisu" buduje serwis odczytu inaczej
    (inny opis, inny schemat), przeszedłby zarówno tu, jak i w macierzy, bo każda z nich patrzy
    tylko na swoją stronę bramki.
    """
    _configure(monkeypatch, tmp_path, bridge=False, jira=False, write=False)
    bez_zapisu = _surface(build_server())
    _configure(monkeypatch, tmp_path, bridge=False, jira=False, write=True)
    z_zapisem = _surface(build_server())

    assert set(z_zapisem) - set(bez_zapisu) == {"save_note"}
    assert {k: v for k, v in z_zapisem.items() if k != "save_note"} == bez_zapisu


def test_note_metadata_contract_is_frozen():
    """Zamrożony jest MODEL, nie tylko sygnatura narzędzia (CLAUDE.md reguła 3).

    Golden-test powierzchni zamraża parametry ``save_note`` (``core/application/tools/mcp.py``),
    a te są RĘCZNĄ kopią pól ``NoteMetadata`` — więc dopisanie pola do modelu nie ruszało żadnego
    schematu. Nowe pole brało wartość domyślną w ``build_note_metadata`` i lądowało we
    frontmatterze każdej nowej notatki w wersjonowanym korpusie, a cały pakiet zostawał zielony
    (sprawdzone mutacją 2026-10-01). Usunięcie pola psuło testy ubocznie, dodanie było darmowe.

    Jeśli ten test pada — zmieniłeś kontrakt danych. To wymaga ADR-a (Bramka 1), a nie
    dopisania pola do listy poniżej.
    """
    # Typ i wymagalność, nie tylko nazwy: `project: str = ""` rozluźniało kontrakt przy zielonym
    # pakiecie — notatka bez projektu przestawała być błędem walidacji.
    kontrakt = {
        nazwa: (pole.annotation, pole.is_required())
        for nazwa, pole in NoteMetadata.model_fields.items()
    }
    assert kontrakt == {
        "title": (str, True),
        "project": (str, True),
        "date": (date, True),
        "participants": (list[str], False),
        "decisions": (list[str], False),
        "action_items": (list[str], False),
        "open_questions": (list[str], False),
        "tags": (list[str], False),
    }


def test_save_note_parameters_match_the_note_metadata_contract():
    """Zamrożona sygnatura ``save_note`` i pola ``NoteMetadata`` nie mogą się rozjechać.

    Ręczna kopia pól w narzędziu ma pokrywać CAŁY kontrakt. Rozjazd w którąkolwiek stronę — pole
    w modelu bez parametru albo parametr bez pola — znaczy, że zapis przez narzędzie przestał
    odwzorowywać schemat notatki. Porównujemy z baseline (a nie z żywym katalogiem), bo to
    dokładnie ten kształt, który widzi model.

    ``body`` jest świadomym wyjątkiem: treść notatki nie należy do frontmatteru.
    """
    params = set(_baseline()["save_note"]["parameters"]["properties"])

    assert params - {"body"} == set(NoteMetadata.model_fields)


def test_baseline_holds_every_tool_the_surface_can_expose():
    """Baseline opisujący podzbiór realnej powierzchni jest gorszy niż brak baseline'u:
    wygląda jak bramka, a przepuszcza wszystko, czego nie wymienia."""
    assert set(_baseline()) == _FROZEN | {_EVENT_TOOL} | _JIRA_TOOLS


def test_new_registrar_forces_matrix_update():
    """Nowy rejestrator zrywa bramkę — bo dokładnie tą drogą weszła kiedyś para Jiry.

    Filtr po ``__module__`` jest istotny: bez niego zaimportowany do modułu ``register_*``
    z innego miejsca dawałby fałszywy sygnał.

    Czego ta asercja NIE łapie — i nie ma udawać, że łapie:
    - ``mcp.add_tool`` wołane wprost w ``server.py``, z pominięciem rejestratora;
    - narzędzie warunkowe WEWNĄTRZ istniejącego katalogu (wzorzec `write_service=None` →
      brak `save_note`) powtórzony pod nową zmienną, wyłączoną w CI;
    - warunek transportowy (`server.py`: para Jiry znika na `streamable-http`).
    """
    registrars = {
        name
        for name, fn in inspect.getmembers(mcp_tools, inspect.isfunction)
        if name.startswith("register_") and fn.__module__ == mcp_tools.__name__
    }

    assert registrars == _COVERED_BY_MATRIX, (
        "zmienił się zestaw rejestratorów MCP — dopisz konfigurację do macierzy powyżej "
        "i zregeneruj baseline, inaczej nowe narzędzie wejdzie na drzwi bez zamrożenia"
    )


def test_file_tool_never_reaches_the_mcp_surface(monkeypatch, tmp_path):
    """``File`` (ADR 0064) jest AGENT-ONLY — na drzwiach MCP nie ma prawa się pojawić.

    Baseline złapałby to i tak, ale asercja nazwana wprost mówi CZEMU: narzędzie materializuje
    plik do kontekstu modelu, a drzwi MCP nie mają ani rozmowy, ani katalogu roboczego, w którym
    ten plik miałby leżeć. Wejście na tę powierzchnię byłoby obietnicą bez pokrycia — i zamrożony
    kontrakt czterech narzędzi przestałby być zamrożony.
    """
    _configure(monkeypatch, tmp_path, bridge=True, jira=True)

    assert "File" not in _surface(build_server())


# Dokument opisujący regenerację baseline'u żyje w drzewie repozytorium, a obraz kopiuje ``src/``
# i ``tests/`` — nie ``docs/``. Rozdzielamy więc sondę na część, która działa WSZĘDZIE (kształt
# zapisu pliku), i część czytającą dokument, pomijaną tam, gdzie dokumentu z założenia nie ma.
# Wzorzec i uzasadnienie: ``tests/test_adr_numbering.py``. Sklejone w jedno, dawały bramkę
# padającą w obrazie z powodu, który NIE JEST usterką — a to najkrótsza droga do wyłączenia
# etapu testowego przy budowie, czyli do utraty bramki naprawdę wartościowej.
_PRZEPIS = 'json.dumps(dane, indent=2, sort_keys=True, ensure_ascii=False) + "\\n"'
_DOC_JAK_DODAC = Path(__file__).resolve().parents[2] / "docs" / "how-to" / "add-a-tool.md"


def test_baseline_ma_ksztalt_ktory_da_sie_odtworzyc_bajt_w_bajt() -> None:
    r"""Kształt zapisu jest CZĘŚCIĄ zamrożonego kontraktu, nie kosmetyką.

    Regeneracja innym kształtem daje diff kilkunastu linii przetasowania i ucieczek `\uXXXX`,
    w którym recenzent nie zobaczy jednej zmienionej frazy — a to przy zamrożonym baseline jest
    jedyna rzecz, na którą ma patrzeć. Ta sonda biegnie WSZĘDZIE, także w obrazie.
    """
    oryginal = _BASELINE.read_text(encoding="utf-8")

    odtworzone = json.dumps(json.loads(oryginal), indent=2, sort_keys=True, ensure_ascii=False)

    assert odtworzone + "\n" == oryginal


@pytest.mark.skipif(
    not _DOC_JAK_DODAC.is_file(),
    reason="docs/ nieobecne — bramka dotyczy drzewa repozytorium, nie obrazu",
)
def test_dokument_niesie_dokladnie_ten_przepis_ktory_dziala() -> None:
    r"""Przepis z `docs/how-to/add-a-tool.md` ma DZIAŁAĆ, nie tylko brzmieć sensownie.

    Pierwsza wersja (2026-09-04) miała samo `sort_keys` — i produkowała dokładnie ten diff, przed
    którym ostrzega: bez `ensure_ascii=False` każda polska litera ucieka do `\uXXXX`, czyli
    szesnaście linii różnicy zamiast jednej. Wyszło przy URUCHOMIENIU przepisu na pliku; lektura
    tego nie łapie, bo brakujący argument wygląda jak brak, nie jak błąd.

    Razem z sondą wyżej wiąże dokument z formatem w obie strony: przeformatowanie pliku zrywa
    tamtą, przeredagowanie przepisu — tę.
    """
    assert _PRZEPIS in _DOC_JAK_DODAC.read_text(encoding="utf-8")
