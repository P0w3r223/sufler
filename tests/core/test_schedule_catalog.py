"""Bramki narzędzia ``Schedule`` — grafik Teams Shifts (ADR 0059; krok 5.4b ADR 0009 paczki).

Krok 5.4b nic tu nie wchłania — narzędzie od początku było jedno. Sondy pilnują dwóch rzeczy,
które ta zmiana wprowadziła:

* **każde pole ma niepusty opis** — dotąd cała proza siedziała w opisie narzędzia, a cztery pola
  szły do modelu z samym ``title`` i ``type``. To było JEDYNE narzędzie agenta łamiące bramkę
  wzorca, więc sonda ma tu wartość regresyjną, nie ozdobną;
* **``week`` jest domknięty ``Literal``em** — niepoprawna wartość przestaje być wyrażalna
  w schemacie. Reguła NIE znika z domeny (``resolve_schedule_range`` dalej ją sprawdza); schemat
  jest pierwszą bramką, domena tą, która obowiązuje. Sonda na wartość spoza enuma sprawdza
  właśnie to: że narzędzie ją przepuszcza do serwisu, zamiast udawać własną walidację.

Zastępuje ``test_team_schedule_catalog.py`` (builder przemianowany razem z narzędziem).
"""

from __future__ import annotations

from typing import Any

from sufler.adapters.outbound.anthropic_llm import _to_tool_def
from sufler.core.application.tools import build_schedule_catalog
from sufler.core.errors import ScheduleReadError


class _FakeSchedule:
    """Atrapa ``TeamScheduleService`` — strukturalnie zgodna, bez portu i realnej strefy."""

    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self._result = result or {"shifts": [], "times_off": []}
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def schedule(
        self, week: str = "current", date_from: str = "", date_to: str = "", person: str = ""
    ) -> dict:
        self.calls.append(
            {"week": week, "date_from": date_from, "date_to": date_to, "person": person}
        )
        if self._error:
            raise self._error
        return self._result


def _zbuduj(**kwargs):
    service = _FakeSchedule(**kwargs)
    return service, build_schedule_catalog(service)[0]  # type: ignore[arg-type]


# ── Zamrożenie schematu ─────────────────────────────────────────────────────────────────


def test_narzedzie_nazywa_sie_schedule_i_nie_ma_pol_wymaganych() -> None:
    """Zdolność jest jedna i ma sensowną wartość domyślną — pytanie „jaki grafik" nie wymaga pól."""
    definicja = _to_tool_def(_zbuduj()[1])
    assert definicja["name"] == "Schedule"
    assert definicja["input_schema"].get("required", []) == []


def test_kazde_pole_ma_niepusty_opis() -> None:
    """Regresja kroku 5.4b: cztery pola szły do modelu z samym `title` i `type`."""
    właściwości = _to_tool_def(_zbuduj()[1])["input_schema"]["properties"]
    assert [n for n, pole in właściwości.items() if not pole.get("description")] == []


def test_schemat_ma_dokladnie_cztery_pola() -> None:
    właściwości = _to_tool_def(_zbuduj()[1])["input_schema"]["properties"]
    assert set(właściwości) == {"week", "date_from", "date_to", "person"}


def test_week_jest_domkniety_enumem_z_domyslnym_biezacym() -> None:
    pole = _to_tool_def(_zbuduj()[1])["input_schema"]["properties"]["week"]
    assert set(pole["enum"]) == {"current", "previous", "next"}
    assert pole["default"] == "current"


# ── Zachowanie ──────────────────────────────────────────────────────────────────────────


def test_zwraca_wynik_serwisu_bez_zmian() -> None:
    wynik = {"shifts": [{"person": "Adam"}], "times_off": []}
    _, spec = _zbuduj(result=wynik)
    assert spec.fn() == wynik


def test_bez_pol_pyta_o_tydzien_biezacy() -> None:
    service, spec = _zbuduj()
    spec.fn()
    assert service.calls == [{"week": "current", "date_from": "", "date_to": "", "person": ""}]


def test_przekazuje_pola_do_serwisu() -> None:
    service, spec = _zbuduj()
    spec.fn(week="next", person="Jerzy Zastepski")
    assert service.calls == [
        {"week": "next", "date_from": "", "date_to": "", "person": "Jerzy Zastepski"}
    ]


def test_pusty_zakres_idzie_do_serwisu_jako_pusty_napis_nie_none() -> None:
    """Serwis i domena mają kontrakt na ``str``; ``None`` z pola opcjonalnego musi się zamienić."""
    service, spec = _zbuduj()
    spec.fn(date_from="2026-08-01", date_to="2026-08-07")
    assert service.calls == [
        {"week": "current", "date_from": "2026-08-01", "date_to": "2026-08-07", "person": ""}
    ]


def test_blad_odczytu_wraca_koperta_a_nie_wyjatkiem() -> None:
    """Brak zgody Schedule.Read.All ma degradować łagodnie — tura nie może się wywrócić."""
    _, spec = _zbuduj(error=ScheduleReadError("brak zgody na grafik"))
    assert "error" in spec.fn()


# ── Instrukcja prezentacji skroconego wyniku w kopercie (ADR 0068 §5) ──────────────────


def test_skrocony_grafik_niesie_ostrzezenie_w_wyniku() -> None:
    """Bez tego zdania `people_without_entries` przeczy skroconym listom — ale w opisie
    jechalo w kazdym zadaniu, takze wtedy, gdy grafik miescil sie w calosci."""
    _, spec = _zbuduj(
        result={"shifts": [], "times_off": [], "truncated": True, "omitted_entries": 12}
    )

    wynik = spec.fn()

    assert "omitted_entries" in wynik["note"]
    assert "people_without_entries" in wynik["note"]


def test_notka_NIE_podwaza_pola_liczonego_z_pelnego_okna() -> None:
    """Notka mówiła modelowi coś odwrotnego niż robi serwis — a to pole jest tu jedynym pewnym.

    ``TeamScheduleService.schedule`` wylicza ``people_without_entries`` z PEŁNYCH list, PRZED
    przycięciem, więc przycięcie zostawia je nienaruszonym. Notka twierdziła, że „dotyczy tylko
    tego, co widać", czyli kazała modelowi zaniżyć zaufanie do jedynego pola, którego skrócenie
    nie dotyka — i to w tej samej odpowiedzi, w której reszta list jest już niepełna.
    """
    _, spec = _zbuduj(
        result={"shifts": [], "times_off": [], "truncated": True, "omitted_entries": 12}
    )

    notka = spec.fn()["note"]

    assert "tylko tego, co widać" not in notka
    assert "CAŁEGO" in notka or "całego okna" in notka


def test_pelny_grafik_nie_dostaje_notki() -> None:
    _, spec = _zbuduj(result={"shifts": [], "times_off": [], "truncated": False})

    assert "note" not in spec.fn()


def test_opis_nie_niesie_juz_regul_prezentacji_skrocenia() -> None:
    assert "omitted_entries" not in _zbuduj()[1].description
