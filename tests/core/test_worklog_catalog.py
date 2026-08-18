"""Propozycja czasu przez ``GitHub(action='worklog')`` (ADR 0034; krok 5.2 ADR 0009 paczki).

Sondy przeniesione z ``build_worklog_catalog``, osieroconego krokiem 5.2. Zachowania są te same
i wszystkie warte utrzymania: koperta zamienia ``InvalidRequestError`` w ``{"error": ...}``
(jedna zła prośba nie wywraca tury), argumenty dochodzą do serwisu, a opis mówi modelowi wprost,
że **nie ma dokąd zapisać godzin** — bez tego zdania model szuka nieistniejącego narzędzia zapisu
(ścieżka ``log_jira_worklog`` została wycięta razem z całą stroną mutującą, ADR 0035).

Asercje na opis biegną teraz po opisie narzędzia ``GitHub``, bo tam ta obietnica żyje.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from workmate.core.application.tools import build_activity_catalog
from workmate.core.domain.worklog import SessionPolicy, build_proposal
from workmate.core.errors import InvalidRequestError


class _FakeEvents:
    def recent(self, *, source=None, project=None, limit=20):
        return []


class _FakeWorklogService:
    """Atrapa serwisu — sterowana wyjątkiem, żeby sprawdzić kopertę bez stawiania portów."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[Any, ...]] = []

    def propose_worklog(self, since: date, until: date, author: str = ""):
        self.calls.append((since, until, author))
        if self.error:
            raise self.error
        return build_proposal([], since=since, until=until, policy=SessionPolicy())


def _zbuduj(error: Exception | None = None):
    service = _FakeWorklogService(error)
    spec = build_activity_catalog(events=_FakeEvents(), worklog=service)[0]  # type: ignore[arg-type]
    return service, spec


def test_worklog_jest_akcja_dopiero_z_serwisem() -> None:
    """Bez serwisu propozycji akcja nie istnieje — obietnica bez pokrycia byłaby regresją."""
    bez = build_activity_catalog(events=_FakeEvents())[0]  # type: ignore[arg-type]
    assert "worklog" not in bez.description
    assert "`worklog`" in _zbuduj()[1].description


def test_opis_mowi_ze_to_odczyt_i_nic_nie_zapisuje() -> None:
    """Model musi wiedzieć, że nie ma dokąd zapisać godzin — inaczej będzie szukał narzędzia."""
    opis = _zbuduj()[1].description
    assert "ODCZYT" in opis
    assert "nic nie zapisuje" in opis


def test_propozycja_serializuje_sie_do_zwyklego_json() -> None:
    _, spec = _zbuduj()
    wynik = spec.fn(action="worklog", since=date(2026, 7, 13), until=date(2026, 7, 19))
    assert wynik["since"] == "2026-07-13"
    assert wynik["sessions"] == []


def test_blad_odczytu_wraca_koperta_a_nie_wyjatkiem() -> None:
    _, spec = _zbuduj(InvalidRequestError("zakres odrzucony"))
    wynik = spec.fn(action="worklog", since=date(2026, 7, 13), until=date(2026, 7, 19))
    assert "error" in wynik


def test_argumenty_dochodza_do_serwisu() -> None:
    service, spec = _zbuduj()
    spec.fn(action="worklog", since=date(2026, 7, 13), until=date(2026, 7, 19), author="P0w3r223")
    assert service.calls[0] == (date(2026, 7, 13), date(2026, 7, 19), "P0w3r223")


# --- Instrukcja prezentacji w kopercie, nie w opisie (ADR 0068 §5) ---------------------


def test_zastrzezenie_o_estymacji_wraca_z_wynikiem() -> None:
    """Zdanie „to estymacja, pokaz disclaimer" jechalo w KAZDYM zadaniu, w opisie narzedzia.

    Potrzebne jest dokladnie raz: wtedy, gdy model patrzy na propozycje. Wzorzec pola `note`
    z `File` — koperta niesie instrukcje obok danych, ktorych dotyczy.
    """
    _, spec = _zbuduj()

    wynik = spec.fn(action="worklog", since=date(2026, 8, 1), until=date(2026, 8, 7))

    assert "ESTYMACJA" in wynik["note"]
    assert "disclaimer" in wynik["note"]
    assert "ESTYMACJA" not in spec.description
