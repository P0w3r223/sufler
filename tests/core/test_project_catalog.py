"""Bramki skonsolidowanego narzędzia ``Project`` (ADR 0009, wzorzec ``action=…``).

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

from sufler.adapters.outbound.anthropic_llm import _to_tool_def
from sufler.core.application.services import (
    NotesWriteService,
    ProjectsService,
)
from sufler.core.application.tools import ToolSpec, build_project_catalog
from sufler.core.domain.models import Project, ProjectStatusRecord
from tests.conftest import FakeNotesRepository, FakeNotesWriter, FakeProjectsRepository


def _projects_service() -> ProjectsService:
    projects = [Project(key="workmate", company="biap", name="Sufler", description="asystent")]
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
    projects = [Project(key="workmate", company="biap", name="Sufler", description="asystent")]
    return NotesWriteService(FakeNotesWriter(), FakeProjectsRepository(projects, {}))


def _spec(*, write: bool) -> ToolSpec:
    return build_project_catalog(
        _projects_service(),
        write_service=_write_service() if write else None,
    )[0]


def _dozwolone_akcje(schema: dict[str, Any]) -> set[str]:
    """Wartości dopuszczone przez schemat — ``enum`` przy wielu, ``const`` przy jednej."""
    action = schema["properties"]["action"]
    if "enum" in action:
        return set(action["enum"])
    return {action["const"]}


# ── Zamrożenie schematu ─────────────────────────────────────────────────────────────────


def test_narzedzie_nazywa_sie_project_i_ma_akcje_wymagana() -> None:
    definicja = _to_tool_def(_spec(write=True))
    assert definicja["name"] == "Project"
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
    assert _dozwolone_akcje(schema) == {"status"}
    assert "save" not in str(schema), "wartość `save` przecieka do schematu wariantu odczytu"


def test_bez_write_service_opis_nie_obiecuje_zapisu() -> None:
    """Bramka w schemacie i opis muszą mówić to samo — inaczej model dostaje sprzeczność.

    Asercja jest na małe litery świadomie: pierwsza wersja sprawdzała wersalikowe „ZAPIS"
    i przepuszczała nagłówek „stan projektu i zapis notatki", który jechał w obu wariantach.
    """
    opis = _spec(write=False).description
    assert "`save`" not in opis
    assert "zapis" not in opis.lower()


# ── Nazwa oddaje zawartość, więc opis nie prostuje nazwy (ADR 0068 §1) ──────────────────


def test_opis_nie_mowi_czego_narzedzie_nie_robi() -> None:
    """Dawne ``Notes`` zużywało 44% opisu na akapit „to narzędzie do tego nie służy".

    Akapit istniał tylko dlatego, że nazwa obiecywała bazę notatek, a narzędzie dawało stan
    jednego projektu. Nazwa zgodna z zawartością kasuje potrzebę prostowania — a przy okazji
    zależność opisu od tego, czy TE drzwi mają powłokę (odesłanie do `sufler-search` albo
    do `search_notes` bywało fałszywe po każdej stronie).
    """
    for opis in (_spec(write=False).description, _spec(write=True).description):
        assert "nie służy" not in opis
        assert "sufler-search" not in opis
        assert "search_notes" not in opis


def test_opis_nie_niesie_sciezek_montazu() -> None:
    """Układ ścieżek mieszka w sekcji ``ENVIRONMENT`` promptu — w dwóch miejscach rozjeżdża się.

    Podpowiedź przy braku `project` niosła `/mnt/system/projects/`, czyli drugą kopię mapy
    montaży, fałszywą na drzwiach bez powłoki.
    """
    opis = _spec(write=True).description
    braki = _spec(write=False).fn(action="status")

    assert "/mnt/" not in opis
    assert "/mnt/" not in braki["hint"]


def test_bez_write_service_pola_zapisu_znikaja_ze_schematu() -> None:
    właściwości = _to_tool_def(_spec(write=False))["input_schema"]["properties"]
    assert set(właściwości) == {"action", "project"}


def test_z_write_service_akcja_save_jest_dostepna() -> None:
    assert _dozwolone_akcje(_to_tool_def(_spec(write=True))["input_schema"]) == {
        "status",
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
    assert wynik["tool"] == "Project"
    assert wynik["action"] == "save"
    assert wynik["missing"] == ["title", "date", "body"]
    assert wynik["hint"]


def test_project_status_bez_projektu_wskazuje_brakujace_pole() -> None:
    wynik = _spec(write=False).fn(action="status")
    assert wynik["missing"] == ["project"]


def test_project_status_zwraca_stan_projektu() -> None:
    wynik = _spec(write=False).fn(action="status", project="workmate")
    assert wynik["key"] == "workmate"
    assert "error" not in wynik


def test_nieistniejacy_projekt_mowi_to_wprost() -> None:
    """Pusty wynik czyta się jak awaria i wywołuje ponowienie — więc go nie zwracamy."""
    wynik = _spec(write=False).fn(action="status", project="nie-ma-takiego")
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


def test_nieznana_akcja_nie_zwraca_po_cichu_stanu_projektu() -> None:
    """Jak w ``Jira``/``GitHub`` — nieznana akcja ma dawać odmowę, nie wynik innej zdolności."""
    wynik = _spec(write=True).fn(action="wymyslona", project="workmate")
    assert wynik["status"] == "invalid_request"
    assert wynik["allowed"] == ["status", "save"]


def test_wariant_odczytu_tez_odmawia_nieznanej_akcji() -> None:
    """Bramka musi być SYMETRYCZNA w obu wariantach buildera.

    Przy `write_service=None` `action='save'` nie istnieje w schemacie, ale wariant bez bramki
    wykonywał dla niej po cichu `project_status`. Zapis się nie wydarzy (nie ma czym), więc
    bramka uprawnień trzymała — psuje się co innego: reguła zaczyna wyglądać na opcjonalną,
    a następna osoba powiela wariant bez niej.
    """
    wynik = _spec(write=False).fn(action="save", project="workmate")
    assert wynik["status"] == "invalid_request"
    assert wynik["allowed"] == ["status"]
    assert "save" not in wynik["hint"], "podpowiedź wymienia akcję, której schemat nie ma"


# --- Ksztalt odpowiedzi "nie znaleziono" (ADR 0068 §9) --------------------------------


def test_zly_klucz_projektu_niesie_tyle_samo_co_brak_klucza() -> None:
    """Model, ktory podal ZLY klucz, dostawal mniej materialu niz ten, ktory nie podal ZADNEGO.

    Brak pola wracal kopertą `tool`/`action`/`hint`, a "projekt nie istnieje" — samym `error`.
    To odwrocona kolejnosc: blizej celu jest ten, kto juz probowal wskazac projekt.
    """
    brak = _spec(write=False).fn(action="status")
    zly = _spec(write=False).fn(action="status", project="nie-ma-takiego")

    assert zly["tool"] == brak["tool"] == "Project"
    assert zly["action"] == brak["action"] == "status"
    assert zly["hint"] == brak["hint"]
    assert zly["status"] == "not_found"
    assert brak["status"] == "invalid_request"


def test_opis_kieruje_pytanie_jak_stoi_projekt() -> None:
    """Zdanie kierujace jest tym, co rozstrzyga wybor miedzy `Project(status)` a `Activity`.

    ADR 0068 stawia `Project` jako narzedzie od kondycji projektu, a `Activity(summary)` od
    przebiegu prac — ale po skroceniu opisu wskazowke mial tylko ten drugi, wiec dla pytania
    „jak stoi projekt X" jawna podpowiedz kierowala do narzedzia, ktore mialo przegrywac.
    """
    opis = _spec(write=False).description

    assert "jak stoi projekt" in opis
