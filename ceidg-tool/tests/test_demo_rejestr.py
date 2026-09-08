"""Atrapa rejestru demo trzymana przy zmierzonych własnościach API (ADR-0014).

Ryzyko trybu demo nie leży w danych — te są generowane, więc nie da się nimi nic wynieść.
Leży w **zachowaniu**: atrapa może się rozjechać z rejestrem i wtedy pokaz uczy nieprawdy,
a nowy właściciel poznaje narzędzie, którego nie ma. Ten projekt zna tę pułapkę z trzech
przypadków (`rokPkd` z ręki, `tests/support.py` z własnym `httpx.Client`, anonimizator
podnoszący identyfikatory do wielkich liter) i ma na nią przyrząd: `tests/fixtures/api_traits.yaml`
mówi, co zmierzono, `tests/test_api_traits.py` trzyma przy tym fixtures — i od teraz także
tę atrapę.

Tutaj leży druga połowa, której traits nie obejmują, bo to nie własności odpowiedzi tylko
dialektu i paginacji: 204 zamiast pustej listy, `count` całego zakresu, koniec stronicowania,
rozmiar partii `ids=`. Każda z nich jest sprawdzana **przez prawdziwego `CeidgClient`**,
a nie przez odczytanie JSON-a atrapy: zgodność atrapy z samą sobą jest właśnie tym, czego
ten plik ma nie dopuścić. Stałe atrapy są dodatkowo przypięte do dostarczonego profilu
środowiska, więc rozjazd z produkcją jest zmianą **dwóch** plików, a nie jednego.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

from ceidg_tool.apiprofile import ApiProfile, load_profile
from ceidg_tool.client import CeidgClient
from ceidg_tool.demo import rejestr as modul_rejestru
from ceidg_tool.demo.korpus import Korpus, zbuduj_korpus, zmienione_w_oknie
from ceidg_tool.demo.rejestr import RejestrDemo
from ceidg_tool.httpclient import build_http_client
from ceidg_tool.ratelimit import InMemoryHistory, RateLimiter
from ceidg_tool.recordid import kanoniczne_id, kanoniczny_id
from tests.conftest import FakeClock
from tests.support import criteria

BASE = "https://test-dane.biznes.gov.pl/api/ceidg/v3"
OKNO_OD = datetime(2026, 9, 1, 0, 0, 0)
OKNO_DO = datetime(2026, 9, 5, 0, 0, 0)


@pytest.fixture(scope="module")
def korpus() -> Korpus:
    return zbuduj_korpus()


@pytest.fixture
def rejestr(korpus: Korpus) -> RejestrDemo:
    return RejestrDemo(korpus)


def profil() -> ApiProfile:
    """Dostarczony profil środowiska `test`, a nie profil pisany w teście.

    Atrapa ma odpowiadać w dialekcie, którym narzędzie naprawdę mówi. Profil zbudowany tutaj
    z ręki pozwoliłby obu stronom rozjechać się z produkcją jednocześnie i zgodnie.
    """
    return load_profile("test", None)


def klient(rejestr: RejestrDemo, clock: FakeClock) -> CeidgClient:
    """`CeidgClient` nad atrapą, złożony tak jak w produkcji — łącznie z bramką wyjścia."""
    wzorzec = profil()
    limiter = RateLimiter(
        windows=wzorzec.rate.windows,
        min_spacing_s=wzorzec.rate.min_spacing_s,
        cooldown_s=wzorzec.rate.cooldown_s,
        clock=clock,
        history=InMemoryHistory(),
    )
    http = build_http_client(
        transport=httpx.MockTransport(rejestr.handler),
        allowed=frozenset({urlsplit(wzorzec.base_url).hostname or ""}),
    )
    return CeidgClient(http=http, profile=wzorzec, limiter=limiter, token="tok", clock=clock)


def zapytaj(rejestr: RejestrDemo, sciezka: str, **params: str) -> httpx.Response:
    """Surowa odpowiedź atrapy — do własności widocznych **na drucie**, nie po parserze.

    `client._records_of` kanonizuje identyfikatory, więc pisownię z `/zmiana` widać wyłącznie
    stąd. Test czytający ją przez klienta sprawdzałby kanonizację, a nie atrapę.
    """
    url = httpx.URL(f"{BASE}/{sciezka}", params=params)
    return rejestr.handler(httpx.Request("GET", url))


def wojewodztwo_wpisow(korpus: Korpus, nazwa: str) -> list[str]:
    return [w.id for w in korpus.wpisy if w.wojewodztwo == nazwa]


# ---------------------------------------------------------------- zgodność z profilem


def test_stale_atrapy_zgadzaja_sie_z_dostarczonym_profilem() -> None:
    """Atrapa dobierająca limity samodzielnie rozjeżdża się z produkcją bez żadnego sygnału.

    Wszystkie cztery liczby są w profilu, bo tam je zapisała sonda fazy 1. Skoro atrapa
    ma udawać ten sam serwer, ma czytać ten sam pomiar — inaczej pokaz stronicuje inaczej
    niż praca, a różnicę widać dopiero u operatora.
    """
    wzorzec = profil()

    assert modul_rejestru.LIMIT_FIRMY == wzorzec.max_limit_firmy
    assert modul_rejestru.LIMIT_ZMIANA == wzorzec.max_limit_zmiana
    assert modul_rejestru.BATCH_IDS == wzorzec.ids_batch_size
    assert 204 in wzorzec.empty_result_statuses


# ---------------------------------------------------------------- pusty wynik i count


def test_brak_trafien_to_204_a_nie_dwiesta_z_pusta_lista(rejestr: RejestrDemo) -> None:
    """Sonda fazy 1 (próbki 11 i 15): pusty wynik to **204 bez treści**.

    Różnica jest nośna, bo `apiprofile.empty_result_statuses` na tym stoi, a `/firmy` z pustą
    listą i `count: 0` przeszłoby przez parser inną gałęzią. Atrapa oddająca 200 nie
    wykonywałaby w demie tej, którą wykonuje produkcja.
    """
    odpowiedz = zapytaj(rejestr, "firmy", nazwa="xqzv-nie-istnieje-9981")

    assert odpowiedz.status_code == 204
    assert odpowiedz.content == b""


def test_count_opisuje_caly_zakres_a_nie_strone(
    rejestr: RejestrDemo, korpus: Korpus, clock: FakeClock
) -> None:
    """Sonda: `count` przy `limit=1` to 6 316 121, czyli wszystkie trafienia.

    Na tym stoi tabela kosztów: gdyby `count` opisywał stronę, wycena mówiłaby „25 firm"
    dla każdego zapytania i pokaz kłamałby o jedynej liczbie, którą operator dostaje
    **przed** wydaniem żądań.
    """
    oczekiwane = len(wojewodztwo_wpisow(korpus, "wielkopolskie"))

    ile = klient(rejestr, clock).count(criteria(wojewodztwo="wielkopolskie"))

    assert ile == oczekiwane > modul_rejestru.LIMIT_FIRMY
    assert rejestr.zadania[-1].endswith("limit=1")


def test_count_zmian_opisuje_okno_a_nie_strone(
    rejestr: RejestrDemo, korpus: Korpus, clock: FakeClock
) -> None:
    """`/zmiana` też: `aktualizuj` kupuje jedno żądanie i zna koszt całego zakresu."""
    oczekiwane = len(zmienione_w_oknie(korpus, OKNO_OD, OKNO_DO))

    ile = klient(rejestr, clock).count_changes(OKNO_OD, OKNO_DO)

    assert ile == oczekiwane > 1


# ---------------------------------------------------------------- stronicowanie


def test_stronicowanie_konczy_sie_i_oddaje_kazdy_wpis_dokladnie_raz(
    rejestr: RejestrDemo, korpus: Korpus, clock: FakeClock
) -> None:
    """Pętla stron ma się zamknąć sama — inaczej pokaz kręci się do `max_pages`.

    `client._iter_paged` rozpoznaje koniec po `links.next == links.self`, więc atrapa,
    która zawsze wskazuje następną stronę, przechodziłaby każdy test o zawartości strony
    i wieszała pobranie.
    """
    oczekiwane = wojewodztwo_wpisow(korpus, "wielkopolskie")

    strony = list(klient(rejestr, clock).iter_pages(criteria(wojewodztwo="wielkopolskie")))

    zebrane = [str(r["id"]) for strona in strony for r in strona.records]
    assert zebrane == oczekiwane
    assert len(zebrane) == len(set(zebrane))
    assert len(strony) == -(-len(oczekiwane) // modul_rejestru.LIMIT_FIRMY)
    assert strony[-1].next_cursor is None


def test_ostatnia_strona_wskazuje_sama_siebie(rejestr: RejestrDemo) -> None:
    """Ta jedna równość jest całym warunkiem końca; wcześniejsze strony muszą się różnić."""
    ostatnia = zapytaj(rejestr, "firmy", wojewodztwo="PODLASKIE", limit="25", page="3")
    pierwsza = zapytaj(rejestr, "firmy", wojewodztwo="PODLASKIE", limit="25", page="0")

    linki_ostatniej = ostatnia.json()["links"]
    linki_pierwszej = pierwsza.json()["links"]

    assert linki_ostatniej["next"] == linki_ostatniej["self"]
    assert linki_pierwszej["next"] != linki_pierwszej["self"]


def test_strona_za_koncem_wyniku_to_204(rejestr: RejestrDemo) -> None:
    """Wznowienie z checkpointu zaczyna od strony, której może już nie być."""
    assert zapytaj(rejestr, "firmy", wojewodztwo="PODLASKIE", page="99").status_code == 204


# ---------------------------------------------------------------- tożsamość wpisu (ADR-0013)


def test_zmiana_oddaje_male_litery_a_firma_wielkie(rejestr: RejestrDemo) -> None:
    """Jeden identyfikator, dwie pisownie — własność, przez którą `aktualizuj` gubił wszystko.

    Sprawdzane na surowej odpowiedzi, bo parser kanonizuje: przez klienta obie strony
    wyglądałyby jednakowo i test byłby o `recordid`, nie o atrapie.
    """
    ze_zmiany = zapytaj(rejestr, "zmiana", dataod="2026-09-01", datado="2026-09-05")
    identyfikatory = ze_zmiany.json()["identyfikatoryWpisow"]

    ze_szczegolow = zapytaj(rejestr, "firma", ids=identyfikatory[0])
    zwrocony = ze_szczegolow.json()["firma"][0]["id"]

    assert identyfikatory == [rid.lower() for rid in identyfikatory]
    assert zwrocony == zwrocony.upper()
    assert zwrocony != identyfikatory[0], "atrapa odbiła pisownię z zapytania"
    assert zwrocony == identyfikatory[0].upper()


def test_firmy_oddaja_identyfikatory_wielkimi_literami(rejestr: RejestrDemo) -> None:
    odpowiedz = zapytaj(rejestr, "firmy", wojewodztwo="PODLASKIE", limit="5")

    identyfikatory = [r["id"] for r in odpowiedz.json()["firmy"]]

    assert identyfikatory == [rid.upper() for rid in identyfikatory]


def test_petla_zmiana_do_firma_odzyskuje_te_same_wpisy(
    rejestr: RejestrDemo, clock: FakeClock
) -> None:
    """Pełna ścieżka `aktualizuj`: identyfikatory z `/zmiana` kupują szczegóły z `/firma`.

    Nocny przebieg z 2026-09-08 kupił 13 401 rekordów za 2 681 żądań i nie pokazał żadnego,
    bo ta pętla nie domykała się na pisowni. Demo musi ją domykać, inaczej pokazuje defekt
    zamiast naprawy — a `missing` jest tu asercją nośną: to on wychodził pusty pobłażliwie.
    """
    api = klient(rejestr, clock)

    identyfikatory = [
        kanoniczny_id(str(r["id"]))
        for strona in api.iter_changes(OKNO_OD, OKNO_DO)
        for r in strona.records
    ]
    rekordy, brakujace = api.fetch_details(identyfikatory)

    assert brakujace == []
    assert {str(r["id"]) for r in rekordy} == set(identyfikatory)
    assert len(rekordy) == len(identyfikatory)


# ---------------------------------------------------------------- partie `ids=`


def test_zbyt_wiele_identyfikatorow_w_jednym_zadaniu_to_400(
    rejestr: RejestrDemo, korpus: Korpus
) -> None:
    """Produkcja odpowiada 400 na 25 i 50 identyfikatorów (sonda fazy 1, `ids_batch_size: 5`).

    Bez tej odmowy atrapa przyjmowałaby dowolną partię, a błąd w dzieleniu na porcje
    ujawniłby się dopiero na produkcji — czyli tam, gdzie odrzucone żądanie i tak zjada limit.
    """
    ids = [w.id for w in korpus.wpisy[: modul_rejestru.BATCH_IDS + 1]]
    url = httpx.URL(f"{BASE}/firma", params={"ids": ids})

    odpowiedz = zapytaj(rejestr, "firma")
    zbyt_duza = rejestr.handler(httpx.Request("GET", url))

    assert odpowiedz.status_code == 400, "żądanie bez `ids` też jest błędem"
    assert zbyt_duza.status_code == 400
    assert "NIEPOPRAWNA_ILOSC" in zbyt_duza.json()["code"]


def test_klient_dzieli_identyfikatory_tak_by_nie_dostac_400(
    rejestr: RejestrDemo, korpus: Korpus, clock: FakeClock
) -> None:
    """Kontrola negatywna do testu wyżej: przy poprawnym dzieleniu 400 nigdy nie pada.

    Gdyby atrapa odpowiadała 400 zbyt chętnie, poprzedni test przechodziłby, a demo
    wywracałoby się na pierwszym pobraniu szczegółów.
    """
    identyfikatory = kanoniczne_id([w.id for w in korpus.wpisy[:17]])

    rekordy, brakujace = klient(rejestr, clock).fetch_details(identyfikatory)

    assert brakujace == []
    assert len(rekordy) == 17
    assert len(rejestr.zadania) == 4  # 17 identyfikatorów po 5 na żądanie


def test_nieznany_identyfikator_to_404(rejestr: RejestrDemo) -> None:
    """404 jest tu ścieżką nośną: `fetch_details` zamienia je na listę `missing`."""
    odpowiedz = zapytaj(rejestr, "firma", ids="00000000-0000-0000-0000-000000000000")

    assert odpowiedz.status_code == 404


def test_klient_zglasza_brak_wpisu_zamiast_go_przemilczec(
    rejestr: RejestrDemo, clock: FakeClock
) -> None:
    nieistniejacy = kanoniczne_id(["00000000-0000-0000-0000-000000000000"])

    rekordy, brakujace = klient(rejestr, clock).fetch_details(nieistniejacy)

    assert rekordy == []
    assert brakujace == nieistniejacy


# ---------------------------------------------------------------- dialekt filtrów


def test_filtr_pkd_obejmuje_kody_poboczne_a_nie_tylko_glowny(
    rejestr: RejestrDemo, korpus: Korpus
) -> None:
    """Zmierzone 2026-09-08: `pkd=` dopasowuje **którykolwiek** kod wpisu.

    Na tym stoi liczba 8,6 % zamiast 25,2 % w audycie luki pokrycia PKD. Atrapa filtrująca
    po samym kodzie głównym uczyłaby o rejestrze rzeczy nieprawdziwej — i to akurat tej,
    która przecenia szkodę o trzy razy.
    """
    z_pobocznym = next(w for w in korpus.wpisy if len(w.pkd) > 1 and w.pkd[-1] != w.pkd_glowny)
    kod = z_pobocznym.pkd[-1]
    # Filtr po samym kodzie głównym oddałby zbiór **niepusty**, tylko bez tego wpisu — więc
    # asercja o niezerowym `count` niczego by nie odróżniała. Chodzi o konkretny wpis.
    tylko_glownym = {w.id for w in korpus.wpisy if w.pkd_glowny.upper() == kod.upper()}

    strony = [zapytaj(rejestr, "firmy", pkd=kod, limit="25", page=str(n)) for n in range(4)]
    pelne = [s for s in strony if s.status_code == 200]
    wszystkie = {r["id"] for s in pelne for r in s.json()["firmy"]}

    assert z_pobocznym.pkd_glowny != kod
    assert z_pobocznym.id not in tylko_glownym
    assert z_pobocznym.id in wszystkie


def test_kod_rocznika_2025_nie_siega_wpisu_zapisanego_rocznikiem_2007(
    rejestr: RejestrDemo,
) -> None:
    """Rejestr jest w połowie przejścia i to jest zdanie, dla którego demo istnieje.

    `9621Z` (2025) i `9602Z` (2007) opisują tę samą branżę. Filtr dopasowuje kod **jak
    zapisano**, więc zbiory są rozłączne — i pokaz ma to pokazać, a nie opowiedzieć.
    """
    z_2025 = zapytaj(rejestr, "firmy", pkd="9621Z", limit="25").json()
    z_2007 = zapytaj(rejestr, "firmy", pkd="9602Z", limit="25").json()

    ident_2025 = {r["id"] for r in z_2025["firmy"]}
    ident_2007 = {r["id"] for r in z_2007["firmy"]}

    assert z_2025["count"] > 0 and z_2007["count"] > 0
    assert ident_2025 & ident_2007 == set()


def test_powtorzony_parametr_znaczy_lub_a_rozne_parametry_znacza_i(
    rejestr: RejestrDemo,
) -> None:
    """Dialekt list z profilu (`list_param_suffix: ""`): `status=A&status=B` to suma."""
    url = httpx.URL(f"{BASE}/firmy", params={"status": ["ZAWIESZONY", "WYKRESLONY"], "limit": "25"})

    laczone = rejestr.handler(httpx.Request("GET", url)).json()
    same_zawieszone = zapytaj(rejestr, "firmy", status="ZAWIESZONY", limit="25").json()

    assert laczone["count"] > same_zawieszone["count"] > 0

    zawezone = zapytaj(rejestr, "firmy", status="ZAWIESZONY", wojewodztwo="PODLASKIE", limit="25")
    assert 0 < zawezone.json()["count"] < same_zawieszone["count"]


def test_bledny_zakres_dat_na_zmianie_to_400(rejestr: RejestrDemo) -> None:
    """`/zmiana` bez zakresu nie ma o czym mówić — i to ma być błąd, nie pusty wynik.

    Klient sam zakresu nie pominie (`_changes_params` zawsze go dokłada), więc ta gałąź
    jest o dialekcie atrapy: 400 jest odpowiedzią rejestru na żądanie bez `dataod`/`datado`,
    a pusty wynik byłby odpowiedzią na żądanie poprawne i bez trafień. Zamiana jednego
    na drugie zmieniłaby błąd w milczący brak danych.
    """
    assert zapytaj(rejestr, "zmiana").status_code == 400
    assert zapytaj(rejestr, "zmiana", dataod="wczoraj", datado="dzis").status_code == 400


# ---------------------------------------------------------------- pozostałe zasoby


def test_raporty_oddaja_pusta_liste_a_nie_blad(rejestr: RejestrDemo, clock: FakeClock) -> None:
    """„Na dziś nie ma gotowego raportu" to normalny stan rejestru, więc `--zrodlo auto` ma wrócić.

    Odpowiedź błędem zamykałaby pokaz zanim się zacznie, i to na ścieżce, którą operator
    dostaje **domyślnie**.
    """
    assert klient(rejestr, clock).list_reports() == []


def test_nieobslugiwany_zasob_odpowiada_404_a_nie_cisza(rejestr: RejestrDemo) -> None:
    """Atrapa ma mówić, czego nie umie — milczenie wygląda jak awaria sieci przez pół godziny."""
    odpowiedz = zapytaj(rejestr, "cokolwiek")

    assert odpowiedz.status_code == 404
    assert "cokolwiek" in odpowiedz.json()["message"]


def test_rejestr_liczy_wydane_zadania(rejestr: RejestrDemo, clock: FakeClock) -> None:
    """Licznik jest tym, co pozwala pokazać na ekranie „zero żądań do CEIDG, N do atrapy"."""
    api = klient(rejestr, clock)

    api.count(criteria(wojewodztwo="podlaskie"))
    api.count(criteria(wojewodztwo="wielkopolskie"))

    assert len(rejestr.zadania) == 2
    assert all(urlsplit(u).path.endswith("/firmy") for u in rejestr.zadania)
    assert all(dict(parse_qsl(urlsplit(u).query))["limit"] == "1" for u in rejestr.zadania)


def test_atrapa_nie_dokleja_pol_ktorych_rejestr_nie_zwraca(rejestr: RejestrDemo) -> None:
    """Rekord z `/firmy` ma być uboższy niż z `/firma`, bo to ta różnica kosztuje żądania."""
    lista = zapytaj(rejestr, "firmy", wojewodztwo="PODLASKIE", limit="1").json()["firmy"][0]
    szczegoly = zapytaj(rejestr, "firma", ids=str(lista["id"])).json()["firma"][0]

    braki: set[str] = set(szczegoly) - set(lista)
    assert {"pkd", "rokPkd", "telefon", "email"} <= braki
    puste: list[Any] = [v for v in lista.values() if v is None]
    assert puste == [], "rejestr pomija pola, nie zwraca ich jako null"
