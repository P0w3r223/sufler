"""Testy jednoźródłowego katalogu narzędzi (ADR 0008) — bramkowanie zapisu.

Katalog jest wspólnym źródłem dla drzwi MCP i runtime'u agenta; kluczowa
niezmiennik to bramka zapisu per drzwi (ADR 0006): ``save_note`` wchodzi tylko
przy ``write_service`` — dokładnie jak ``register_tools(write_service=None)``.
"""

from __future__ import annotations

from tests.conftest import (
    FakeNotesRepository,
    FakeNotesWriter,
    FakeProjectsRepository,
)
from workmate.core.application.services import (
    NotesService,
    NotesWriteService,
    ProjectsService,
)
from workmate.core.application.tools import (
    build_agent_notes_read_catalog,
    build_tool_catalog,
    przemianuj_na_konwencje_agenta,
)
from workmate.core.domain.models import Project

_READ_TOOLS = {"search_notes", "get_note", "list_projects", "get_project_status"}


def _services(*, with_write: bool):
    notes_repo = FakeNotesRepository([])
    projects_repo = FakeProjectsRepository(
        [Project(key="scada-integration", company="mpwik", name="X", description="")],
        records={},
    )
    notes = NotesService(notes_repo)
    projects = ProjectsService(projects_repo, notes_repo)
    write = NotesWriteService(FakeNotesWriter(), projects_repo) if with_write else None
    return notes, projects, write


def test_catalog_is_read_only_without_write_service():
    notes, projects, _ = _services(with_write=False)

    names = {spec.name for spec in build_tool_catalog(notes, projects)}

    assert names == _READ_TOOLS


def test_catalog_includes_save_note_with_write_service():
    notes, projects, write = _services(with_write=True)

    names = {spec.name for spec in build_tool_catalog(notes, projects, write_service=write)}

    assert names == _READ_TOOLS | {"save_note"}


def test_catalog_specs_carry_the_docstring_as_the_description():
    """Opis narzędzia = docstring funkcji (jedno źródło) — model widzi to, co czyta człowiek."""
    notes, projects, _ = _services(with_write=False)

    specs = {spec.name: spec for spec in build_tool_catalog(notes, projects)}

    assert specs["search_notes"].description.startswith("Przeszukaj notatki")
    assert specs["get_note"].description.startswith("Pobierz pełną treść")
    assert specs["list_projects"].description.startswith("Wypisz projekty")


def test_no_tool_outside_the_write_gate_can_change_the_knowledge_base():
    """„Odczyt domyślny" (CLAUDE.md #2) mierzone od strony NAZW, nie od strony bramki.

    Sonda powyżej pilnuje, że przy ``write_service=None`` katalog równa się zbiorowi odczytu.
    Ta pilnuje twierdzenia mocniejszego i odporniejszego na dołożenie narzędzia: przy zamkniętej
    bramce w katalogu NIE MA nazwy sugerującej mutację — także takiej, której dziś nie znamy.
    """
    notes, projects, _ = _services(with_write=False)

    names = [spec.name for spec in build_tool_catalog(notes, projects)]

    zakazane = ("save", "write", "edit", "delete", "update", "remove")
    assert not [n for n in names if any(slowo in n.lower() for slowo in zakazane)]


def test_the_read_tools_are_the_same_three_the_agent_gets_without_a_shell():
    """Trójka odczytu agenta to POCZĄTEK ``build_tool_catalog`` — z jedną różnicą: NAZWĄ.

    Docstring fabryki opiera na tym całe uzasadnienie wydzielenia (dwóch konsumentów, jedno
    źródło), a pilnował tego dotąd wyłącznie golden powierzchni MCP — czyli test, który
    zauważyłby rozjazd dopiero po stronie drzwi. Rozjazd tutaj znaczyłby, że agent bez powłoki
    czyta bazę wiedzy INNYMI narzędziami niż sesja MCP.

    Od ADR 0068 §3 powierzchnia agenta ma jedną konwencję nazw (PascalCase), a powierzchnia MCP
    zostaje zamrożona — więc zgodność jest MODULO MAPA NAZW, a nie bajt w bajt. Mapa obejmuje
    też odwołania w prozie: opis ``get_note`` odsyła po identyfikator „z wyników search_notes",
    czyli do narzędzia, którego pod tą nazwą na powierzchni agenta NIE MA (amendment ADR 0068).

    Sonda porównuje opis MCP przepuszczony przez tę samą mapę z opisem agenta. Porównanie
    surowych napisów przepuściłoby zerwanie przemianowania odwołań; porównanie samych nazw
    przepuściłoby rozjazd treści.
    """
    notes, projects, _ = _services(with_write=False)

    wydzielone = build_agent_notes_read_catalog(notes, projects)
    pelny = build_tool_catalog(notes, projects)

    # Porównujemy (nazwa, opis) — ``fn`` to inne domknięcie z każdej fabryki, więc same
    # ``ToolSpec`` nigdy nie są równe; zachowanie porównuje sonda niżej, wołając obie drogi.
    przemianowane = [przemianuj_na_konwencje_agenta(s) for s in pelny[: len(wydzielone)]]
    assert [(s.name, s.description) for s in przemianowane] == [
        (s.name, s.description) for s in wydzielone
    ]
    assert [s.name for s in wydzielone] == ["SearchNotes", "GetNote", "ListProjects"]
    assert [s.name for s in pelny[: len(wydzielone)]] == [
        "search_notes",
        "get_note",
        "list_projects",
    ]


def test_odwolania_w_opisie_ida_ta_sama_mapa_co_nazwy():
    """Regres, który przeżył pierwszą rundę ADR 0068: `GetNote` odsyłał do `search_notes`.

    „Jedno źródło opisu" i „bez odwołań do nieobecnych narzędzi" zderzały się tutaj i pierwsza
    wygrała po cichu — bo sonda odwołań znała wyłącznie NOWE nazwy, więc starego napisu nie
    widziała. Ta asercja patrzy z drugiej strony: w opisie agenta nie ma prawa zostać ŻADNA
    stara nazwa, a odesłanie ma wskazywać narzędzie, które agent faktycznie dostaje.
    """
    notes, projects, _ = _services(with_write=False)

    agent = {s.name: s.description for s in build_agent_notes_read_catalog(notes, projects)}
    mcp = {s.name: s.description for s in build_tool_catalog(notes, projects)}

    assert "search_notes" in mcp["get_note"], "MCP zachowuje swoją nazwę — inaczej ruszył golden"
    assert "SearchNotes" in agent["GetNote"]
    for opis in agent.values():
        for stara in ("search_notes", "get_note", "list_projects"):
            assert stara not in opis, f"stara nazwa {stara!r} przeżyła w opisie agenta"


def test_the_read_tools_behave_identically_through_both_factories():
    """Zgodność opisów to za mało — sonda woła OBIE drogi i porównuje wynik."""
    notes, projects, _ = _services(with_write=False)

    z_wydzielonej = {s.name: s.fn for s in build_agent_notes_read_catalog(notes, projects)}
    z_pelnej = {s.name: s.fn for s in build_tool_catalog(notes, projects)}

    assert z_wydzielonej["ListProjects"]() == z_pelnej["list_projects"]()
    assert z_wydzielonej["GetNote"]("nie/ma/takiej") == z_pelnej["get_note"]("nie/ma/takiej")
    assert z_wydzielonej["SearchNotes"]("scada") == z_pelnej["search_notes"]("scada")


def test_get_note_reports_a_missing_note_as_a_readable_error_not_as_none():
    """Model dostaje słownik, nie ``None`` — „nie ma takiej notatki" ma być do przeczytania."""
    notes, projects, _ = _services(with_write=False)
    (get_note,) = [s.fn for s in build_tool_catalog(notes, projects) if s.name == "get_note"]

    assert "nie istnieje" in get_note("mpwik/scada-integration/2026-01-01-nie-ma")["error"].lower()


def test_przemianowanie_degraduje_do_nazwy_mcp_zamiast_kłaść_drzwi():
    """Mapa nazw jest DRUGIM źródłem obok buildera współdzielonego z zamrożoną powierzchnią MCP.

    Czwarte narzędzie odczytu dodane po tamtej stronie nie ma wpisu w mapie konwencji agenta,
    a indeksowanie ``_NAZWY_AGENTA[spec.name]`` wywracało wtedy składanie CAŁYCH drzwi agenta
    na ``KeyError`` — z powodu kosmetycznego. Degradacja zostawia model z nazwą spoza konwencji:
    to widać i da się poprawić, w przeciwieństwie do drzwi, które się nie podniosły.
    """
    from workmate.core.application.tools import ToolSpec

    nowe = ToolSpec("get_project_status", "Opis czwartego narzędzia odczytu.", lambda: {})

    przemianowane = przemianuj_na_konwencje_agenta(nowe)

    assert przemianowane.name == "get_project_status"
    assert przemianowane.description == nowe.description
