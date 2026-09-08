"""Kreator zapisuje plik zapytania **po** decyzjach, nie przed (ADR-0012, sub-decyzja 5).

ADR obiecuje, że plik zapisany przez `offer_yaml` niesie faktyczny wybór operatora i powtarza
przebieg co do kodu. Do 2026-09-07 nie było to prawdą: `handle_fetch` proponowało zapis zaraz po
zebraniu kryteriów, czyli **przed** pytaniem o rocznik PKD i przed wyborem „lista czy szczegóły".
Harmonogram uruchamiał więc inne zapytanie niż to, które operator przed chwilą zatwierdził —
i to bez żadnego błędu po drodze, bo plik był poprawny, tylko opisywał wcześniejszy stan.

Znalezione przy pisaniu testów do ADR-0012. Test jest tu, a nie w `test_wizard_menu.py`, bo
potrzebuje `deps` z tablicą przejścia, a tamten plik celowo jej nie ma.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import yaml

from ceidg_tool.config import Settings
from ceidg_tool.criteria import Criteria
from ceidg_tool.pipeline import Deps, build_deps
from ceidg_tool.ui import wizard
from ceidg_tool.ui.prompts import ScriptedPrompter
from tests.conftest import FakeClock
from tests.support import FakeApi, RecordingView, pkd_map

WASKI_COUNT = 38_201
SZEROKI_COUNT = 225_350

PUSTE: dict[str, object] = {
    "wojewodztwo": "",
    "miasto": "",
    "pkd": "",
    "status": "",
    "data_od": "",
    "data_do": "",
    "nazwa": "",
    "max_rekordow": "",
}


def vintage_api() -> FakeApi:
    """`count` zależny od zestawu kodów — dwie populacje muszą dać dwie różne liczby."""
    api = FakeApi()

    def fallback(request: httpx.Request) -> httpx.Response:
        if "/firmy" in str(request.url) and request.url.params.get("limit") == "1":
            kody = set(request.url.params.get_list("pkd"))
            count = SZEROKI_COUNT if "9602Z" in kody else WASKI_COUNT
            return httpx.Response(200, json={"count": count, "firmy": []})
        raise AssertionError(f"nieoczekiwane pobranie: {request.url}")

    api.fallback = fallback
    return api


def deps_with_map(tmp_path: Path, clock: FakeClock, api: FakeApi) -> Deps:
    settings = Settings(token="tok", environment="test", data_dir=tmp_path / "dane")
    deps = build_deps(settings, clock=clock, http=api.client())
    deps.pkd_map = pkd_map()
    return deps


def test_the_saved_query_file_carries_the_vintage_the_operator_chose(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Plik zapisany po wyborze „szerzej" musi ten wybór nieść — inaczej powtórzenie kłamie.

    To jest cała treść obietnicy z ADR-0012: „a query file written by `offer_yaml` records the
    operator's actual choice and reruns it identically". Bez `pkd_2007` w pliku harmonogram
    pobierałby węższą populację niż ta, której koszt operator zatwierdził na ekranie.
    """
    deps = deps_with_map(tmp_path, clock, vintage_api())
    prompter = ScriptedPrompter(
        {
            **PUSTE,
            "miasto": "Łomża",
            "pkd": "9621Z",
            "rocznik_pkd": "szerokie",
            "zapisz_yaml": True,
            # Szersza populacja przekracza próg 50 tys., więc program pyta o podział, a nie
            # o „co dalej" — to zachowanie sprzed ADR-0012, wywołane tu przez większy `count`.
            "podzial": "wyjdz",
        }
    )

    wizard.handle_fetch(deps, prompter, RecordingView(), source="auto")

    (zapisany,) = list(deps.settings.output_dir.glob("*.yaml"))
    dane = yaml.safe_load(zapisany.read_text(encoding="utf-8"))
    assert dane["pkd"] == ["9621Z"]
    assert dane["pkd_2007"] == ["9602Z"], "wybór rocznika nie trafił do pliku zapytania"
    # Plik ma się wczytać z powrotem do tych samych kryteriów, nie tylko wyglądać poprawnie.
    # Kolejność jest posortowana, bo `_dedupe` sortuje — dzięki temu odcisk palca nie zależy
    # od tego, w jakiej kolejności kody trafiły do kryteriów.
    assert Criteria.model_validate(dane).wszystkie_pkd() == ("9602Z", "9621Z")
    deps.store.close()


def test_the_saved_query_file_records_a_narrow_choice_as_narrow(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Wybór wąski zapisuje się przez **nieobecność** pola, więc powtórzenie też nie poszerza.

    `exclude_defaults=True` w `offer_yaml` znaczy, że puste `pkd_2007` znika z pliku — a to
    dobrze: plik bez tego pola przechodzi przez `_kandydaci` normalną drogą i… zapytałby
    ponownie. Test pilnuje, że przynajmniej nie zapisuje się cudzy wybór.
    """
    deps = deps_with_map(tmp_path, clock, vintage_api())
    prompter = ScriptedPrompter(
        {
            **PUSTE,
            "miasto": "Łomża",
            "pkd": "9621Z",
            "rocznik_pkd": "waskie",
            "zapisz_yaml": True,
            "co_dalej": "wyjdz",
        }
    )

    wizard.handle_fetch(deps, prompter, RecordingView(), source="auto")

    (zapisany,) = list(deps.settings.output_dir.glob("*.yaml"))
    dane = yaml.safe_load(zapisany.read_text(encoding="utf-8"))
    assert "pkd_2007" not in dane
    deps.store.close()


def test_refusing_to_choose_a_vintage_does_not_offer_to_save_that_non_choice(
    tmp_path: Path, clock: FakeClock
) -> None:
    """„Wróć do menu" przy roczniku nie może prowadzić do pytania o zapis pliku zapytania.

    Po przeniesieniu `offer_yaml` za decyzje (ADR-0012) obie odmowy — „wyjdź bez pobierania"
    i „wróć do menu przy roczniku" — wracały tą samą wartością `wyjdz`, więc kreator
    proponował zapis także tej drugiej. Zapisałby wtedy `waskie`, czyli wybór, którego
    operator właśnie odmówił dokonać, a plik obiecuje powtórzyć **jego** decyzję.
    """
    deps = deps_with_map(tmp_path, clock, vintage_api())
    prompter = ScriptedPrompter(
        {**PUSTE, "miasto": "Łomża", "pkd": "9621Z", "rocznik_pkd": "wyjdz"}
    )

    wizard.handle_fetch(deps, prompter, RecordingView(), source="auto")

    assert "zapisz_yaml" not in prompter.asked
    assert list(deps.settings.output_dir.glob("*.yaml")) == []
    deps.store.close()
