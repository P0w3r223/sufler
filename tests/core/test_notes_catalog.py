"""Bramki skonsolidowanego narzędzia ``Notes`` (ADR 0009, wzorzec ``action=…``).

Dwie z nich obowiązują od tego kroku na każdym następnym narzędziu tej serii:

* **zamrożenie ``input_schema``** wraz z NIEPUSTYMI opisami pól — bez tego następne
  narzędzie wróci do gołych adnotacji, a cały argument za wzorcem (opisy pól przechodzą
  przez ``func_metadata``, pomiar 1 w ADR 0009) przestanie być egzekwowany;
* **sonda negatywna na bramkę zapisu** — przy ``write_service=None`` wartość ``save``
  nie ma prawa istnieć w schemacie. Bramka przepuszczająca wszystko wygląda identycznie
  jak działająca, dopóki ktoś nie sprawdzi strony odmownej (ta sama lekcja co 0/6 sond
  na usługę ``exec`` w paczce wdrożeniowej).

Jednowartościowy ``Literal`` renderuje się jako ``const``, nie ``enum`` — asercje muszą
obejmować oba kształty, inaczej sonda przechodzi przez pomyłkę, a nie przez poprawność.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from tests.conftest import FakeNotesRepository, FakeNotesWriter, FakeProjectsRepository
from workmate.adapters.outbound.anthropic_llm import _to_tool_def
from workmate.core.application.services import (
    NotesWriteService,
    ProjectsService,
)
from workmate.core.application.tools import ToolSpec, build_notes_catalog
from workmate.core.domain.models import Project, ProjectStatusRecord


def _projects_service() -> ProjectsService:
    projects = [Project(key="workmate", company="biap", name="WorkMate", description="asystent")]
    records = {
        "workmate": ProjectStatusRecord(
            key="workmate",
            status="active",
            health="green",
            phase="Faza 1",
            summary="W toku",
            last_updated=date(2026, 8, 1),
        )
    }
    return ProjectsService(FakeProjectsRepository(projects, records), FakeNotesRepository([]))


def _write_service() -> NotesWriteService:
    projects = [Project(key="workmate", company="biap", name="WorkMate", description="asystent")]
    return NotesWriteService(FakeNotesWriter(), FakeProjectsRepository(projects, {}))


def _spec(*, write: bool, shell: bool = False) -> ToolSpec:
    return build_notes_catalog(
        _projects_service(),
        write_service=_write_service() if write else None,
        shell_available=shell,
    )[0]


def _dozwolone_akcje(schema: dict[str, Any]) -> set[str]:
    """Wartości dopuszczone przez schemat — ``enum`` przy wielu, ``const`` przy jednej."""
    action = schema["properties"]["action"]
    if "enum" in action:
        return set(action["enum"])
    return {action["const"]}


# ── Zamrożenie schematu ─────────────────────────────────────────────────────────────────


def test_narzedzie_nazywa_sie_notes_i_ma_akcje_wymagana() -> None:
    definicja = _to_tool_def(_spec(write=True))
    assert definicja["name"] == "Notes"
    assert definicja["input_schema"]["required"] == ["action"]


def test_kazde_pole_ma_niepusty_opis() -> None:
    """Opisy pól są całym powodem, dla którego wzorzec ``action=…`` jest do przyjęcia.

    Gdy znikną, jedno grube narzędzie niesie mniej informacji niż kilka wąskich, które
    zastąpiło — a schemat wygląda tak samo poprawnie.
    """
    właściwości = _to_tool_def(_spec(write=True))["input_schema"]["properties"]
    bez_opisu = [nazwa for nazwa, pole in właściwości.items() if not pole.get("description")]
    assert bez_opisu == []


def test_pola_zapisu_sa_w_schemacie_wariantu_zapisu() -> None:
    właściwości = _to_tool_def(_spec(write=True))["input_schema"]["properties"]
    assert {"project", "title", "date", "body"} <= set(właściwości)


# ── Sonda negatywna: bramka zapisu ──────────────────────────────────────────────────────


def test_bez_write_service_akcja_save_nie_istnieje_w_schemacie() -> None:
    schema = _to_tool_def(_spec(write=False))["input_schema"]
    assert _dozwolone_akcje(schema) == {"project_status"}
    assert "save" not in str(schema), "wartość `save` przecieka do schematu wariantu odczytu"


def test_bez_write_service_opis_nie_obiecuje_zapisu() -> None:
    """Bramka w schemacie i opis muszą mówić to samo — inaczej model dostaje sprzeczność.

    Asercja jest na małe litery świadomie: pierwsza wersja sprawdzała wersalikowe „ZAPIS"
    i przepuszczała nagłówek „stan projektu i zapis notatki", który jechał w obu wariantach.
    """
    opis = _spec(write=False).description
    assert "`save`" not in opis
    assert "zapis" not in opis.lower()


# ── Opis odsyła tam, gdzie zdolność faktycznie jest ─────────────────────────────────────


def test_bez_powloki_opis_odsyla_do_narzedzi_odczytu() -> None:
    """Odesłanie do `workmate-search` bez `Bash` byłoby obietnicą bez pokrycia — i to w stanie
    DOMYŚLNYM produkcji, gdzie `WORKMATE_ENABLE_SHELL` jest wyłączona (ADR 0010)."""
    opis = _spec(write=False, shell=False).description
    assert "search_notes" in opis and "get_note" in opis
    assert "workmate-search" not in opis


def test_z_powloka_opis_odsyla_do_rankera_w_powloce() -> None:
    opis = _spec(write=False, shell=True).description
    assert "workmate-search" in opis
    assert "search_notes" not in opis


def test_bez_write_service_pola_zapisu_znikaja_ze_schematu() -> None:
    właściwości = _to_tool_def(_spec(write=False))["input_schema"]["properties"]
    assert set(właściwości) == {"action", "project"}


def test_z_write_service_akcja_save_jest_dostepna() -> None:
    assert _dozwolone_akcje(_to_tool_def(_spec(write=True))["input_schema"]) == {
        "project_status",
        "save",
    }


# ── Walidacja per akcja ─────────────────────────────────────────────────────────────────


def test_brak_pola_wymaganego_przez_akcje_daje_blad_strukturalny() -> None:
    """JSON Schema nie wyrazi „przy ``save`` wymagany jest ``title``" — robi to dispatcher.

    Odpowiedź ma nieść WSZYSTKO, czego trzeba do poprawienia wywołania, żeby model nie
    musiał czytać schematu drugi raz.
    """
    wynik = _spec(write=True).fn(action="save", project="workmate")
    assert wynik["status"] == "invalid_request"
    assert wynik["tool"] == "Notes"
    assert wynik["action"] == "save"
    assert wynik["missing"] == ["title", "date", "body"]
    assert wynik["hint"]


def test_project_status_bez_projektu_wskazuje_brakujace_pole() -> None:
    wynik = _spec(write=False).fn(action="project_status")
    assert wynik["missing"] == ["project"]


def test_project_status_zwraca_stan_projektu() -> None:
    wynik = _spec(write=False).fn(action="project_status", project="workmate")
    assert wynik["key"] == "workmate"
    assert "error" not in wynik


def test_nieistniejacy_projekt_mowi_to_wprost() -> None:
    """Pusty wynik czyta się jak awaria i wywołuje ponowienie — więc go nie zwracamy."""
    wynik = _spec(write=False).fn(action="project_status", project="nie-ma-takiego")
    assert "nie istnieje w rejestrze" in wynik["error"]


def test_save_dopisuje_notatke() -> None:
    wynik = _spec(write=True).fn(
        action="save",
        project="workmate",
        title="Przegląd",
        date=date(2026, 8, 6),
        body="Ustalenia.",
    )
    assert wynik["saved"] is True
    assert wynik["id"].startswith("biap/workmate/2026-08-06")
