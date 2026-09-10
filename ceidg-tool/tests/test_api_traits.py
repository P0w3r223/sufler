"""Fixtures muszą zgadzać się ze zmierzonymi własnościami API (`tests/fixtures/api_traits.yaml`).

Po co osobny plik z twierdzeniami, skoro fixtures są kopią prawdziwych odpowiedzi: bo kopia
przechodzi przez `scripts/anonymize_samples.py`, a anonimizator może własność skasować —
i skasował. Podnosił każdy identyfikator do wielkich liter, więc `/zmiana` w fixture wyglądał
jak `/firma`, cała suita offline zgadzała się co do świata, którego nie ma, a na produkcji
`aktualizuj` zapisywał każdą zmienioną firmę dwa razy i nie pokazywał żadnej (ADR-0013).

Twierdzenie zapisane osobno od próbki zamyka pętlę: fixture nie może zostać wygenerowany do
zgodności z samym sobą, bo musi zgodzić się z pomiarem. Audyt na dole konfrontuje ten pomiar
z surowymi próbkami, gdy są pod ręką.

Od 2026-09-08 temu samemu pomiarowi podlega **druga** atrapa: syntetyczny rejestr trybu demo
(ADR-0014). Powód jest ten sam co przy fixtures, tylko droga inna — fixture traci własność
przez anonimizator, atrapa przez to, że nikt jej z rejestrem nie porównał. Stawka jest za to
wyższa: demo nie stoi w suicie, tylko na ekranie, i jest jedyną drogą, którą nowy właściciel
ogląda narzędzie w ruchu. To, czego atrapa nie umie udawać, staje się tym, czego on nie wie
o rejestrze.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from ceidg_tool.apiprofile import load_profile
from ceidg_tool.demo.korpus import zbuduj_korpus
from ceidg_tool.demo.rejestr import RejestrDemo
from ceidg_tool.recordid import GUID_WPISU, kanoniczny_id

FIXTURES = Path(__file__).parent / "fixtures"
PROBE_OUT = Path(__file__).resolve().parents[1] / "probe_out"
PROBKI = PROBE_OUT / "samples"
TRAITS = yaml.safe_load((FIXTURES / "api_traits.yaml").read_text(encoding="utf-8"))
KSZTALT_GUID = re.compile(
    r"^[0-9A-Za-z]{8}-[0-9A-Za-z]{4}-[0-9A-Za-z]{4}-[0-9A-Za-z]{4}-[0-9A-Za-z]{12}$"
)

IDENTYFIKATORY: list[dict[str, Any]] = TRAITS["identyfikatory"]
ID_PARAMS = [pytest.param(t, id=str(t["endpoint"])) for t in IDENTYFIKATORY]
ODPOWIEDZI: dict[str, dict[str, Any]] = {t["wlasnosc"]: t for t in TRAITS["odpowiedzi"]}


def _ids(body: Any, sciezka: list[str]) -> list[str]:
    """Identyfikatory spod ścieżki: `[klucz]` — lista napisów, `[klucz, pole]` — lista rekordów."""
    raw = body[sciezka[0]]
    if len(sciezka) == 1:
        return [str(v) for v in raw]
    return [str(r[sciezka[1]]) for r in raw]


def _wielkosc(value: str) -> str:
    litery = [c for c in value if c.isalpha()]
    if not litery:
        return "bez_liter"
    if all(c.isupper() for c in litery):
        return "upper"
    if all(c.islower() for c in litery):
        return "lower"
    return "mieszana"


@pytest.mark.parametrize("trait", ID_PARAMS)
def test_fixture_zgadza_sie_ze_zmierzona_wlasnoscia(trait: dict[str, Any]) -> None:
    body = json.loads((FIXTURES / trait["fixture"]).read_text(encoding="utf-8"))["body"]
    ids = _ids(body, list(trait["sciezka"]))
    assert ids, f"fixture {trait['fixture']} nie niesie żadnego identyfikatora"
    for rid in ids:
        assert KSZTALT_GUID.fullmatch(rid), (
            f"{trait['endpoint']}: {rid!r} nie ma kształtu 8-4-4-4-12"
        )
        czy_hex = GUID_WPISU.fullmatch(rid) is not None
        assert czy_hex == (trait["ksztalt"] == "guid_hex"), (
            f"{trait['endpoint']}: {rid!r} — szesnastkowość niezgodna z pomiarem"
        )
    if trait["wielkosc_liter"] != "mieszana":
        zmierzone = {_wielkosc(rid) for rid in ids} - {"bez_liter"}
        assert zmierzone == {trait["wielkosc_liter"]}, (
            f"{trait['endpoint']}: fixture niesie pisownię {zmierzone}, "
            f"pomiar mówi {trait['wielkosc_liter']!r} — czy anonimizator jej nie ujednolicił?"
        )


def test_pomiar_opisuje_obie_pisownie() -> None:
    """Detektor spłaszczenia: gdyby wszystkie endpointy niosły tę samą pisownię, cały ten
    plik przestałby cokolwiek chronić, a defekt wróciłby niezauważony."""
    pisownie = {t["wielkosc_liter"] for t in IDENTYFIKATORY}
    assert {"upper", "lower"} <= pisownie
    assert {t["endpoint"] for t in IDENTYFIKATORY} == {"firmy", "firma", "zmiana", "raporty"}


@pytest.mark.parametrize("trait", ID_PARAMS)
def test_kanonizacja_rusza_tylko_identyfikatory_wpisow(trait: dict[str, Any]) -> None:
    """GUID wpisu idzie do wielkich liter, identyfikator raportu przechodzi nietknięty."""
    body = json.loads((FIXTURES / trait["fixture"]).read_text(encoding="utf-8"))["body"]
    for rid in _ids(body, list(trait["sciezka"])):
        if trait["ksztalt"] == "guid_hex":
            assert kanoniczny_id(rid) == rid.upper()
        else:
            assert kanoniczny_id(rid) == rid


@pytest.mark.parametrize("trait", ID_PARAMS)
def test_audyt_pomiaru_na_surowych_probkach(trait: dict[str, Any]) -> None:
    """Konfrontacja twierdzenia z produkcją — pomijana uczciwie, gdy `probe_out/` nie ma.

    `probe_out/` jest poza repozytorium (niesie dane osobowe), więc w CI ten test się pomija.
    Lokalnie, gdzie próbki są, sprawdza to, czego fixture z definicji nie udowodni: że pomiar
    opisuje odpowiedź rejestru, a nie tylko naszą jej przeróbkę."""
    probka = PROBKI / str(trait["probka"])
    if not probka.exists():
        pytest.skip(f"brak surowej próbki {probka.name} — audyt wymaga probe_out/")
    body = json.loads(probka.read_text(encoding="utf-8"))["body"]
    ids = _ids(body, list(trait["sciezka"]))
    assert ids
    for rid in ids:
        assert (GUID_WPISU.fullmatch(rid) is not None) == (trait["ksztalt"] == "guid_hex")
    if trait["wielkosc_liter"] != "mieszana":
        assert {_wielkosc(rid) for rid in ids} - {"bez_liter"} == {trait["wielkosc_liter"]}


# ------------------------------------------------------- własności odpowiedzi (nie identyfikatory)


def _fixture_body(nazwa: str) -> tuple[int, Any]:
    surowe = json.loads((FIXTURES / nazwa).read_text(encoding="utf-8"))
    return int(surowe["status"]), surowe.get("body")


def test_fixture_pustego_wyniku_niesie_zmierzony_status_i_pusta_tresc() -> None:
    """204 bez treści, nie 200 z pustą listą — i profil ma znać dokładnie tę wartość.

    Bez tego pomiar istniałby tylko w `docs/decisions.md`, a jedyną jego kopią w kodzie
    byłby profil, którego nikt z niczym nie porównuje.
    """
    cecha = ODPOWIEDZI["pusty_wynik"]

    status, tresc = _fixture_body(str(cecha["fixture"]))

    assert status == cecha["status"]
    assert (tresc is not None) == cecha["ma_tresc"]
    assert cecha["status"] in load_profile("test", None).empty_result_statuses


def test_fixture_count_opisuje_caly_zakres_a_nie_strone() -> None:
    """`count` to liczba trafień, więc przy `limit=1` musi przekraczać rozmiar strony."""
    cecha = ODPOWIEDZI["count"]

    _, tresc = _fixture_body(str(cecha["fixture"]))

    assert cecha["semantyka"] == "total" == load_profile("test", None).count_semantics
    assert tresc["count"] > len(tresc[cecha["endpoint"]]) == 1


# ------------------------------------------------------------- atrapa trybu demo (ADR-0014)

# Syntetyczny rejestr podlega temu samemu pomiarowi co fixtures i z tego samego powodu.
# Fixture mogła zgubić własność przez anonimizator; atrapa może ją zgubić przez to, że
# nikt jej nigdy nie porównał z rejestrem — a demo jest dziś jedyną drogą, którą nowy
# właściciel widzi narzędzie w ruchu, więc jego wyobrażenie o API pochodzi stąd.

DEMO_BASE = "https://test-dane.biznes.gov.pl/api/ceidg/v3"
# Endpointy, które atrapa obsługuje, i zapytanie oddające dla każdego z nich identyfikatory.
# `raporty` nie ma tu pozycji świadomie: demo oddaje pustą listę raportów (patrz niżej),
# więc nie ma czego trzymać przy pomiarze — i lepiej, żeby ten brak był zapisany, niż
# żeby test cicho przechodził na zbiorze pustym.
DEMO_ZAPYTANIA: dict[str, dict[str, str]] = {
    "firmy": {"wojewodztwo": "PODLASKIE", "limit": "5"},
    "zmiana": {"dataod": "2026-09-01", "datado": "2026-09-05"},
}


def _demo_odpowiedz(rejestr: RejestrDemo, endpoint: str, **params: str) -> httpx.Response:
    url = httpx.URL(f"{DEMO_BASE}/{endpoint}", params=params)
    return rejestr.handler(httpx.Request("GET", url))


@pytest.fixture(name="demo_rejestr")
def demo_rejestr_fixture() -> RejestrDemo:
    return RejestrDemo(zbuduj_korpus(ile=40))


@pytest.mark.parametrize("trait", ID_PARAMS)
def test_atrapa_demo_zgadza_sie_ze_zmierzona_pisownia(
    trait: dict[str, Any], demo_rejestr: RejestrDemo
) -> None:
    """Ta sama parametryzacja, ten sam pomiar, druga strona — atrapa zamiast próbki.

    Pisownia identyfikatorów jest własnością, którą anonimizator już raz skasował
    z materiału dowodowego (ADR-0013). Atrapa demo powstała po tym zdarzeniu i deklaruje,
    że go nie powtarza; ten test jest miejscem, w którym deklaracja przestaje być deklaracją.
    """
    endpoint = str(trait["endpoint"])
    if endpoint == "firma":
        # `/firma` pyta się identyfikatorami, więc zapytanie buduje się z poprzedniego kroku.
        zrodlo = _demo_odpowiedz(demo_rejestr, "zmiana", **DEMO_ZAPYTANIA["zmiana"])
        maly_id = str(zrodlo.json()["identyfikatoryWpisow"][0])
        odpowiedz = _demo_odpowiedz(demo_rejestr, "firma", ids=maly_id)
    elif endpoint in DEMO_ZAPYTANIA:
        odpowiedz = _demo_odpowiedz(demo_rejestr, endpoint, **DEMO_ZAPYTANIA[endpoint])
    else:
        pytest.skip(f"atrapa demo nie obsługuje /{endpoint} — patrz DEMO_ZAPYTANIA")

    ids = _ids(odpowiedz.json(), list(trait["sciezka"]))

    assert ids, f"atrapa demo nie oddała żadnego identyfikatora dla /{endpoint}"
    for rid in ids:
        assert KSZTALT_GUID.fullmatch(rid), f"{endpoint}: {rid!r} nie ma kształtu 8-4-4-4-12"
        assert (GUID_WPISU.fullmatch(rid) is not None) == (trait["ksztalt"] == "guid_hex")
    assert {_wielkosc(rid) for rid in ids} - {"bez_liter"} == {trait["wielkosc_liter"]}, (
        f"{endpoint}: atrapa demo niesie inną pisownię niż zmierzono na produkcji"
    )


def test_atrapa_demo_nie_odbija_pisowni_z_zapytania(demo_rejestr: RejestrDemo) -> None:
    """Kontrola dopełniająca: to `ids=` dopasowuje bez względu na wielkość liter, nie odpowiedź.

    Atrapa, która oddaje identyfikator w pisowni, o którą ją zapytano, spełni test wyżej
    dla `/zmiana` i dla `/firma` osobno, a mimo to nie pokaże niczego o tożsamości wpisu —
    dokładnie tak zachowywały się atrapy sprzed ADR-0013.
    """
    maly_id = str(
        _demo_odpowiedz(demo_rejestr, "zmiana", **DEMO_ZAPYTANIA["zmiana"]).json()[
            "identyfikatoryWpisow"
        ][0]
    )

    odpowiedz = _demo_odpowiedz(demo_rejestr, "firma", ids=maly_id)

    zwrocony = str(odpowiedz.json()["firma"][0]["id"])
    assert zwrocony == maly_id.upper() != maly_id


def test_atrapa_demo_zgadza_sie_z_wlasnosciami_odpowiedzi(demo_rejestr: RejestrDemo) -> None:
    """Pusty wynik i `count` — te same dwa zdania, którym podlegają fixtures."""
    pusty = ODPOWIEDZI["pusty_wynik"]
    licznik = ODPOWIEDZI["count"]

    brak = _demo_odpowiedz(demo_rejestr, "firmy", nazwa="xqzv-nie-istnieje-9981")
    strona = _demo_odpowiedz(demo_rejestr, "firmy", wojewodztwo="PODLASKIE", limit="5")

    assert brak.status_code == pusty["status"]
    assert (brak.content != b"") == pusty["ma_tresc"]
    assert licznik["semantyka"] == "total"
    assert strona.json()["count"] > len(strona.json()["firmy"]) == 5


def test_koniec_stronicowania_zmierzony_na_probkach_spoza_samples() -> None:
    """`links.next == links.self` na ostatniej stronie — zmierzone, nie wywnioskowane.

    Ten test istnieje, bo ta własność była z `api_traits.yaml` **wypisana** z uzasadnieniem,
    że sonda nigdy nie dotarła do ostatniej strony `count = 6 316 121` (audyt architektury
    2026-09-09, F-E1). Do ostatniej strony sześciomilionowego wyniku nie trzeba docierać:
    przy `count = 1` jedyna strona jest zarazem ostatnią. Obie próbki leżą poza
    `probe_out/samples/`, w które celuje `PROBKI` — i to jedno zawężenie wskaźnika wystarczyło,
    żeby pomiar przestał być widoczny.

    Pomijany bez `probe_out/`, tak samo i z tego samego powodu co `test_audyt_pomiaru_...`.
    """
    cecha = ODPOWIEDZI["koniec_stronicowania"]

    sprawdzone = 0
    for wzgledna in cecha["probki_probe_out"]:
        probka = PROBE_OUT / str(wzgledna)
        if not probka.exists():
            continue
        body = json.loads(probka.read_text(encoding="utf-8"))["body"]
        links = body["links"]
        assert (links["next"] == links["self"]) == cecha["next_rowne_self"], (
            f"{wzgledna}: strona końcowa nie zachowuje się jak zmierzono"
        )
        sprawdzone += 1

    if not sprawdzone:
        pytest.skip("brak surowych próbek strony końcowej — audyt wymaga probe_out/")


def test_atrapa_demo_konczy_stronicowanie_tak_jak_rejestr(demo_rejestr: RejestrDemo) -> None:
    """Druga strona tego samego pomiaru — atrapa zamiast próbki, i ta biegnie w CI.

    Bez tego nowa własność byłaby twierdzeniem, którego nic nie czyta poza katalogiem
    nieobecnym w CI. Kształt zapytania jest celowo ten sam co w pomiarze: filtr zwracający
    jeden wpis, więc jedyna strona **jest** ostatnią.

    Stawka jest konkretna: atrapa oddająca `next` wskazujący wciąż kolejną stronę zapętla
    pobieranie aż do `max_pages`, czyli 10 000 żądań i około dziesięciu godzin.
    """
    strona = _demo_odpowiedz(demo_rejestr, "firmy", **DEMO_ZAPYTANIA["firmy"]).json()
    nip = str(strona["firmy"][0]["wlasciciel"]["nip"])

    jedna = _demo_odpowiedz(demo_rejestr, "firmy", nip=nip).json()

    assert jedna["count"] == len(jedna["firmy"]) == 1, "filtr po NIP miał zwrócić jeden wpis"
    links = jedna["links"]
    assert (links["next"] == links["self"]) == ODPOWIEDZI["koniec_stronicowania"]["next_rowne_self"]


def test_atrapa_demo_nie_udaje_raportow_ktorych_nie_ma(demo_rejestr: RejestrDemo) -> None:
    """Powód, dla którego `/raporty` nie ma pozycji w `DEMO_ZAPYTANIA`, jest tu zapisany.

    Pusta lista, a nie 404: brak gotowego raportu jest normalnym stanem rejestru i `--zrodlo
    auto` ma się wtedy cofnąć do API. Gdyby atrapa zaczęła kiedyś oddawać raporty, ich
    identyfikatory podlegałyby własnemu pomiarowi (nie-hex, istotne co do wielkości liter),
    więc ten test pilnuje, że nowa zdolność nie wejdzie bez pokrycia.
    """
    odpowiedz = _demo_odpowiedz(demo_rejestr, "raporty")

    assert odpowiedz.status_code == 200
    assert odpowiedz.json() == {"raporty": []}
    assert "raporty" not in DEMO_ZAPYTANIA
