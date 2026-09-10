"""Kreator pokazuje polecenie **po** decyzjach, nie przed (ADR-0012 sub-decyzja 5, ADR-0022).

ADR-0012 obiecuje, że powtórzenie niesie faktyczny wybór operatora. Do 2026-09-07 nie było to
prawdą: `handle_fetch` proponowało zapis zaraz po zebraniu kryteriów, czyli **przed** pytaniem
o rocznik PKD i przed wyborem „lista czy szczegóły". Harmonogram uruchamiał więc inne zapytanie
niż to, które operator przed chwilą zatwierdził — i to bez żadnego błędu po drodze, bo zapis był
poprawny, tylko opisywał wcześniejszy stan.

Nośnikiem powtórzenia był plik zapytania YAML; od 2026-09-10 jest nim gotowe polecenie
(ADR-0022). Obietnica się nie zmieniła, zmienił się jej kształt — i **jedna** rzecz merytorycznie:
plik niósł konkretne kody z rocznika 2007, polecenie niesie przełącznik `--pkd-2007`, więc kody
dobiera tablica przejścia w chwili uruchomienia. Zdanie o tym stoi na ekranie
(`texts.POLECENIE_ROCZNIK`) i jest sprawdzone niżej.

Test jest tu, a nie w `test_wizard_menu.py`, bo potrzebuje `deps` z tablicą przejścia, a tamten
plik celowo jej nie ma.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from ceidg_tool.config import Settings
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


def linia_polecenia(view: RecordingView) -> str:
    """Sama linia `ceidg-tool pobierz …` z ekranu powtórzenia.

    Asercje muszą dotyczyć **polecenia**, nie całego przebiegu: ekran o roczniku PKD wymienia
    kody 2007 z nazwami, więc „9602Z nie występuje w tekście" byłoby fałszem niezależnie od
    tego, co niesie polecenie.
    """
    linie = [w for w in view.text().splitlines() if "ceidg-tool pobierz" in w]
    assert len(linie) == 1, f"spodziewano się jednej linii polecenia, jest {len(linie)}"
    return linie[0]


def deps_with_map(tmp_path: Path, clock: FakeClock, api: FakeApi) -> Deps:
    settings = Settings(token="tok", environment="test", data_dir=tmp_path / "dane")
    deps = build_deps(settings, clock=clock, http=api.client())
    deps.pkd_map = pkd_map()
    return deps


def test_polecenie_niesie_rocznik_ktory_operator_wybral(tmp_path: Path, clock: FakeClock) -> None:
    """Polecenie pokazane po wyborze „szerzej" musi ten wybór nieść — inaczej powtórzenie kłamie.

    Bez `--pkd-2007` harmonogram pobierałby węższą populację niż ta, której koszt operator
    zatwierdził na ekranie — a `--tak` zawęża sam z siebie, więc pominięcie przełącznika nie
    byłoby nawet widoczne jako pytanie bez odpowiedzi.
    """
    deps = deps_with_map(tmp_path, clock, vintage_api())
    view = RecordingView()
    prompter = ScriptedPrompter(
        {
            **PUSTE,
            "miasto": "Łomża",
            "pkd": "9621Z",
            "rocznik_pkd": "szerokie",
            # Szersza populacja przekracza próg 50 tys., więc program pyta o podział, a nie
            # o „co dalej" — to zachowanie sprzed ADR-0012, wywołane tu przez większy `count`.
            "podzial": "wyjdz",
        }
    )

    wizard.handle_fetch(deps, prompter, view, source="auto")

    polecenie = linia_polecenia(view)
    assert "--pkd-2007" in polecenie, "wybór rocznika nie trafił do polecenia"
    assert "--pkd 9621Z" in polecenie
    # Kod z rocznika 2007 **nie** wchodzi jako wartość: nie ma dla niego flagi listowej, a
    # przełącznik znaczy „dobierz je z tablicy". Ta różnica wobec pliku zapytania ma stać
    # na ekranie, a nie w niczyjej pamięci.
    assert "9602Z" not in polecenie
    assert "tablica przejścia" in view.text()
    deps.store.close()


def test_waski_wybor_zostaje_waski_w_poleceniu(tmp_path: Path, clock: FakeClock) -> None:
    """Wybór wąski zapisuje się przez **nieobecność** przełącznika, więc powtórzenie nie poszerza.

    Kontrola przeciwna do poprzedniej: polecenie, które zawsze dokleja `--pkd-2007`, przeszłoby
    tamten test i po cichu zmieniało populację każdemu, kto wybrał wąsko.
    """
    deps = deps_with_map(tmp_path, clock, vintage_api())
    view = RecordingView()
    prompter = ScriptedPrompter(
        {
            **PUSTE,
            "miasto": "Łomża",
            "pkd": "9621Z",
            "rocznik_pkd": "waskie",
            "co_dalej": "wyjdz",
        }
    )

    wizard.handle_fetch(deps, prompter, view, source="auto")

    assert "--pkd-2007" not in linia_polecenia(view)
    assert "tablica przejścia" not in view.text()  # uwaga dotyczy tylko wyboru szerokiego
    deps.store.close()


def test_odmowa_wyboru_rocznika_nie_pokazuje_polecenia(tmp_path: Path, clock: FakeClock) -> None:
    """„Wróć do menu" przy roczniku nie może skończyć się poleceniem do powtórzenia.

    Po przeniesieniu powtórzenia za decyzje (ADR-0012) obie odmowy — „wyjdź bez pobierania"
    i „wróć do menu przy roczniku" — wracały tą samą wartością `wyjdz`, więc kreator pokazywał
    powtórzenie także tej drugiej. Niosłoby ono `waskie`, czyli wybór, którego operator właśnie
    odmówił dokonać, a powtórzenie obiecuje **jego** decyzję.
    """
    deps = deps_with_map(tmp_path, clock, vintage_api())
    view = RecordingView()
    prompter = ScriptedPrompter(
        {**PUSTE, "miasto": "Łomża", "pkd": "9621Z", "rocznik_pkd": "wyjdz"}
    )

    wizard.handle_fetch(deps, prompter, view, source="auto")

    assert "ceidg-tool pobierz" not in view.text()
    deps.store.close()
