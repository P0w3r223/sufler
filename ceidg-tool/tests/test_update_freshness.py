"""Świeżość szczegółów w `aktualizuj` — czy „zmienił się" wygrywa z „mam to w cache" (A1).

Audyt 2026-09-08, pozycja A1. `aktualizuj` bierze identyfikatory z `/zmiana`, czyli ze zdania
rejestru „te wpisy się zmieniły", a potem przepuszczał je przez siedmiodniowy TTL cache'u.
Wpis zmieniony wczoraj, a pobrany trzy dni temu, mieścił się w TTL i był pomijany: jego
`detail_json` sprzed zmiany zostawał w bazie, a podsumowanie meldowało go jako odświeżony.
Na bazie operatora 742 z 2 891 identyfikatorów (25,7 %) powtórzyło się między dwoma
przebiegami odległymi o 23 godziny. `count_run_unresolved` tego nie widzi i nie może: wpis
jest w stanie `pobrany`, czyli **rozwiązany, tylko nieprawdziwy**.

Defekt był zamaskowany do 2026-09-08 — dopóki ADR-0013 nie naprawił pisowni identyfikatorów,
cache nie trafiał ani razu, więc filtr TTL zwracał wszystko. Naprawa jednej cichej straty
odsłoniła drugą, więc ten plik pyta o objaw wprost: **jaka treść stoi w bazie po przebiegu**,
a nie ile żądań poszło.

Każdy test tutaj był sprawdzony przez zepsucie mechanizmu, który opisuje (przywrócenie progu
TTL w `stale_detail_ids`, wyzerowanie `outdated_details`, usunięcie odmowy dla `--do`
z przyszłości, pominięcie `until` w `cli.aktualizuj`). Test, który przy wyłączonym mechanizmie
zostaje zielony, jest materiałem dowodowym bez możliwości zawiedzenia — a tego kształtu ten
projekt ma już trzy egzemplarze.

Zegar atrap stoi **za** końcem okna zmian, bo rejestr nie zgłasza zmian, które się jeszcze nie
wydarzyły. Domyślny `FakeClock` z `conftest` startuje 2023-11-14, więc w parze z oknem z 2026
znaczyłby „szczegół musi pochodzić z przyszłości" — i test mierzyłby coś innego, niż deklaruje.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import ceidg_tool.config as config
from ceidg_tool import cli
from ceidg_tool.cli import _koniec_zakresu_zmian, app
from ceidg_tool.clock import SystemClock, utc_iso
from ceidg_tool.config import Settings
from ceidg_tool.errors import ConfigError
from ceidg_tool.pipeline import (
    Deps,
    RunResult,
    build_deps,
    run_update,
    update_range,
    update_scope,
)
from ceidg_tool.progress import Events, NullEvents
from ceidg_tool.recordid import kanoniczne_id
from ceidg_tool.store import Store
from ceidg_tool.ui import texts
from tests.conftest import FakeClock, detail_record
from tests.support import FakeApi, registry_id

# Jeden wpis wystarcza: defekt dotyczy każdego identyfikatora osobno, a jeden pozwala pytać
# o jego treść, a nie o sumę.
WPIS = "18578BAF-BAC7-42B9-AA7E-4C3666132E01"
# Okno krótsze niż `UPDATE_WINDOW_DAYS`, więc `[od, do]` to jedno okno i próg świeżości
# szczegółu jest równy `do` — bez tego test opisywałby podział na okna, nie próg.
OD = datetime(2026, 9, 7, tzinfo=UTC)
DO = datetime(2026, 9, 8, tzinfo=UTC)
# Przebieg rusza po domknięciu okna — jedyny stan, jaki na produkcji występuje.
TERAZ = datetime(2026, 9, 8, 6, tzinfo=UTC)
PRZED_ZMIANA = "Piekarnia Adam Nowak"
PO_ZMIANIE = "Piekarnia Adam Nowak — zakład 2"


class Rejestr:
    """Atrapa `/zmiana` i `/firma` dla jednego wpisu, w pisowni, którą rejestr **zwraca**.

    `/zmiana` oddaje identyfikatory małymi literami, `/firma` wielkimi, niezależnie od pisowni
    zapytania (`tests/fixtures/api_traits.yaml`). Atrapa odbijająca pisownię z zapytania zgadza
    się sama ze sobą i nie może pokazać niczego o tożsamości wpisu (ADR-0013)."""

    def __init__(self, nazwa: str = PO_ZMIANIE) -> None:
        self.nazwa = nazwa
        self.pytano_o_szczegoly: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/zmiana" in url:
            adres = "https://test-dane.biznes.gov.pl/api/ceidg/v3/zmiana?strona=1"
            return httpx.Response(
                200,
                json={
                    "identyfikatoryWpisow": [WPIS.lower()],
                    "count": 1,
                    # `next == self` domyka paginację po jednej stronie (`client._iter_paged`).
                    "links": {"self": adres, "next": adres},
                },
            )
        if "/firma" in url:
            pytane = request.url.params.get_list("ids")
            self.pytano_o_szczegoly.extend(pytane)
            return httpx.Response(
                200,
                json={
                    "firma": [
                        {**detail_record(1), "id": registry_id(rid), "nazwa": self.nazwa}
                        for rid in pytane
                    ]
                },
            )
        return httpx.Response(404, json={"code": "BRAK", "message": url})


def _deps(tmp_path: Path, clock: FakeClock, rejestr: Rejestr) -> Deps:
    api = FakeApi()
    api.fallback = rejestr.handler
    settings = Settings(token="tok", environment="test", data_dir=tmp_path / "dane")
    return build_deps(settings, clock=clock, http=api.client())


def _magazyn(tmp_path: Path, clock: FakeClock) -> Store:
    return Store(tmp_path / "s.sqlite", environment="test", clock=clock)


def _run_zmiany(store: Store) -> str:
    return store.start_run(
        run_id="r",
        criteria_json="{}",
        criteria_hash="zmiana",
        profile_hash="p",
        mode="szczegoly",
        tool_version="0",
        cursor_mode="links",
        kind="zmiana",
    )


def _nazwa_w_bazie(deps: Deps, run_id: str) -> str:
    """Treść, którą zobaczy eksport — jedyny świadek, którego nie da się przekonać licznikiem."""
    rekord = next(r for r in deps.store.iter_run_records(run_id))
    assert rekord.detail_json is not None, "wpis runu został bez szczegółów"
    return str(rekord.detail_json["nazwa"])


# --------------------------------------------------------------- próg świeżości w magazynie


def test_szczegol_sprzed_progu_jest_nieswiezy_choc_miesci_sie_w_ttl(tmp_path: Path) -> None:
    """Rdzeń A1: „mam to sprzed trzech dni" przestaje być odpowiedzią na „to się zmieniło".

    Szczegół sprzed trzech dni mieści się w siedmiodniowym TTL cache'u, więc stary filtr go
    przepuszczał. Próg podany przez wołającego — koniec okna zmian — uznaje go za nieświeży,
    bo opisuje stan sprzed zmiany, którą właśnie zgłosił rejestr."""
    clock = FakeClock(start_wall=(TERAZ - timedelta(days=3)).timestamp())
    with _magazyn(tmp_path, clock) as store:
        store.save_details(details=[{**detail_record(1), "id": WPIS}])
        clock.advance(3 * 86_400)  # dziś rejestr zgłasza ten wpis jako zmieniony

        stale = store.stale_detail_ids(kanoniczne_id([WPIS]), cutoff=DO)

    assert stale == [WPIS]


def test_szczegol_dokladnie_z_progu_jest_swiezy(tmp_path: Path) -> None:
    """Równość po stronie świeżości, i to nie jest kosmetyka.

    Szczegół pobrany dokładnie w momencie progu jest tym, co zostawia po sobie przebieg
    domykający okno. Gdyby próg działał ostro, powtórka tego samego, zamkniętego okna kupowała
    tę samą treść od nowa — czyli naprawa cichej straty zamieniłaby się w cichy koszt."""
    clock = FakeClock(start_wall=DO.timestamp())
    with _magazyn(tmp_path, clock) as store:
        store.save_details(details=[{**detail_record(1), "id": WPIS}])

        stale = store.stale_detail_ids(kanoniczne_id([WPIS]), cutoff=DO)

    assert stale == []


# ------------------------------------------------------------------ obserwator cichej straty


def test_licznik_widzi_wpis_ktory_zostal_z_opisem_sprzed_zmiany(tmp_path: Path) -> None:
    """Gwarancja bez obserwatora nie jest gwarancją — tu jest obserwator.

    Wpis stoi w stanie `pobrany` z treścią sprzed zakresu zmian, więc żaden licznik
    „brakujących" go nie pokaże. Drugi próg (sprzed samego szczegółu) pilnuje, że licznik
    porównuje z podanym momentem, a nie liczy wszystkiego, co run dotknął."""
    clock = FakeClock(start_wall=(TERAZ - timedelta(days=3)).timestamp())
    with _magazyn(tmp_path, clock) as store:
        run_id = _run_zmiany(store)
        store.link_ids(run_id, page_index=0, ids=kanoniczne_id([WPIS]))
        store.save_details(details=[{**detail_record(1), "id": WPIS}])
        clock.advance(3 * 86_400)

        ids = kanoniczne_id([WPIS])
        alarm = len(store.outdated_details(ids, older_than=OD))
        cisza = len(store.outdated_details(ids, older_than=TERAZ - timedelta(days=4)))

    assert (alarm, cisza) == (1, 0)


def test_licznik_nie_liczy_wpisu_nieznanego_rejestrowi(tmp_path: Path) -> None:
    """„Nie ma takiego wpisu" to wyjaśnienie, a nie opis sprzed zmiany.

    Stan `nieznaleziony` też niesie stary `detail_utc`, bo zapisuje się go tym samym
    poleceniem. Gdyby licznik patrzył na czas bez stanu, każdy wykreślony wpis dokładałby
    alarm — a alarm, który dzwoni przy normalnej pracy, przestaje coś znaczyć."""
    clock = FakeClock(start_wall=(TERAZ - timedelta(days=3)).timestamp())
    with _magazyn(tmp_path, clock) as store:
        run_id = _run_zmiany(store)
        store.link_ids(run_id, page_index=0, ids=kanoniczne_id([WPIS]))
        store.save_details(details=[], missing_ids=kanoniczne_id([WPIS]))
        clock.advance(3 * 86_400)

        alarm = len(store.outdated_details(kanoniczne_id([WPIS]), older_than=OD))

    assert alarm == 0


def test_licznik_pyta_o_wskazane_identyfikatory_a_nie_o_cala_baze(tmp_path: Path) -> None:
    """Alarm należy do okna, które właśnie się domknęło.

    Baza pamięta wszystkie wcześniejsze pobrania, więc licznik bez zawężenia meldowałby
    cudzą, spokojnie leżącą historię jako świeżą awarię — przy każdym `aktualizuj`.
    Zakres idzie po identyfikatorach, nie po runie, bo próg świeżości jest **per okno**,
    a okno zna swoje `ids` i swój koniec; `run_firma` zna tylko numer strony."""
    inny = "AAAAAAAA-1111-2222-3333-444444444444"
    clock = FakeClock(start_wall=(TERAZ - timedelta(days=3)).timestamp())
    with _magazyn(tmp_path, clock) as store:
        run_id = _run_zmiany(store)
        store.link_ids(run_id, page_index=0, ids=kanoniczne_id([WPIS, inny]))
        store.save_details(
            details=[{**detail_record(1), "id": WPIS}, {**detail_record(1), "id": inny}]
        )
        clock.advance(3 * 86_400)

        cudzy = len(store.outdated_details(kanoniczne_id([inny]), older_than=OD))
        wlasny = len(store.outdated_details(kanoniczne_id([WPIS]), older_than=OD))
        oba = len(store.outdated_details(kanoniczne_id([WPIS, inny]), older_than=OD))

    # Każdy z osobna jest przestarzały, ale pytanie o jeden nie może odpowiadać za drugi.
    assert (cudzy, wlasny, oba) == (1, 1, 2)


# ------------------------------------------------------------------- cała ścieżka `aktualizuj`


def _z_cache(tmp_path: Path, *, szczegol_z: datetime) -> tuple[Deps, Rejestr]:
    """Baza z jednym wpisem, którego szczegół pobrano `szczegol_z`, i zegar na `TERAZ`.

    Szczegół zapisuje osobny `Store` z własnym, cofniętym zegarem, zamiast przesuwania zegara
    `Deps` w trakcie. Przesunięcie działałoby tak samo, ale zapalałoby przy okazji detektor snu
    maszyny (`LockHeartbeat`) — czyli test hałasowałby ostrzeżeniem o zdarzeniu, którego nie
    bada, a ostrzeżenie widziane w każdym przebiegu przestaje być ostrzeżeniem."""
    rejestr = Rejestr()
    deps = _deps(tmp_path, FakeClock(start_wall=TERAZ.timestamp()), rejestr)
    zegar_cache = FakeClock(start_wall=szczegol_z.timestamp())
    with Store(deps.settings.store_path, environment="test", clock=zegar_cache) as cache:
        cache.save_details(details=[{**detail_record(1), "id": WPIS, "nazwa": PRZED_ZMIANA}])
    return deps, rejestr


def _przebieg(tmp_path: Path, *, szczegol_z: datetime) -> tuple[Deps, Rejestr, RunResult]:
    """Wpis ze szczegółem pobranym `szczegol_z`, potem `aktualizuj` po oknie `[OD, DO]`."""
    deps, rejestr = _z_cache(tmp_path, szczegol_z=szczegol_z)
    return deps, rejestr, run_update(deps, since=OD, until=DO)


def test_wpis_zgloszony_jako_zmieniony_dostaje_nowy_opis_mimo_swiezego_cache(
    tmp_path: Path,
) -> None:
    """Objaw produkcyjny wprost: w bazie zostawał opis sprzed zmiany.

    Szczegół sprzed trzech dni był w TTL, więc `aktualizuj` go pomijał, a podsumowanie liczyło
    ten wpis jako odświeżony. Asercja jest o treści w bazie — to ona idzie do skoroszytu,
    i to ona była nieprawdziwa, mimo że wszystkie liczniki się zgadzały."""
    deps, rejestr, wynik = _przebieg(tmp_path, szczegol_z=TERAZ - timedelta(days=3))

    assert rejestr.pytano_o_szczegoly, "rejestr nie został zapytany o zmieniony wpis"
    assert _nazwa_w_bazie(deps, wynik.run_id) == PO_ZMIANIE
    assert wynik.details == 1
    deps.store.close()


def test_licznik_nieswiezych_po_udanym_przebiegu_jest_zerem(tmp_path: Path) -> None:
    """Zero jest jedyną poprawną wartością — i jest po co je pokazywać.

    Ten sam przebieg, co wyżej, ale pytany o obserwatora: gdyby wpis został z opisem sprzed
    zakresu zmian, `unresolved` milczałby (stan `pobrany`), a `stale_details` policzy."""
    deps, _, wynik = _przebieg(tmp_path, szczegol_z=TERAZ - timedelta(days=3))

    assert wynik.stale_details == 0
    deps.store.close()


def test_alarm_dzwoni_gdy_filtr_swiezosci_znowu_zacznie_pomijac_zmienione(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nawrót A1 wstrzyknięty na szwie, którym wszedł — bo zero bez alarmu wygląda jak zero.

    Poprawny przebieg zawsze melduje `stale_details == 0`, więc sam ten fakt nie odróżnia
    działającego obserwatora od skasowanego. Tutaj filtr świeżości udaje, że wszystko jest
    świeże — dokładnie to robił siedmiodniowy TTL dla wpisu pobranego trzy dni temu — i pytamy,
    czy ktokolwiek to zauważy. Objaw i alarm są sprawdzane razem: w bazie zostaje opis sprzed
    zmiany, a `unresolved` milczy, bo wpis jest w stanie `pobrany`."""
    deps, rejestr = _z_cache(tmp_path, szczegol_z=TERAZ - timedelta(days=3))
    monkeypatch.setattr(deps.store, "stale_detail_ids", lambda ids, *, cutoff: [])

    wynik = run_update(deps, since=OD, until=DO)

    assert rejestr.pytano_o_szczegoly == []
    assert _nazwa_w_bazie(deps, wynik.run_id) == PRZED_ZMIANA
    assert (wynik.stale_details, wynik.unresolved) == (1, 0)
    deps.store.close()


def test_szczegol_pobrany_w_srodku_okna_tez_jest_kupowany_od_nowa(tmp_path: Path) -> None:
    """Próg to koniec okna, nie jego początek i nie „ostatnia doba".

    Szczegół ze środka okna jest młodszy niż początek zakresu, a mimo to opisuje stan sprzed
    zmiany zgłoszonej na końcu okna. Test pyta o **treść w bazie**, bo treść jest objawem:
    licznik może być zerem z dwóch powodów — bo nic nie zostało pominięte albo bo nikt nie
    policzył — a nazwa pod identyfikatorem ma tylko jedno wyjaśnienie."""
    deps, rejestr, wynik = _przebieg(tmp_path, szczegol_z=datetime(2026, 9, 7, 12, tzinfo=UTC))

    assert rejestr.pytano_o_szczegoly, "wpis zmieniony w środku okna nie został odświeżony"
    assert _nazwa_w_bazie(deps, wynik.run_id) == PO_ZMIANIE
    deps.store.close()


def test_powtorka_domknietego_okna_nie_kupuje_szczegolow_drugi_raz(tmp_path: Path) -> None:
    """Druga połowa rachunku: naprawa nie ma prawa zamienić się w kupowanie wszystkiego co run.

    Zegar między przebiegami idzie do przodu, bo próg równy „teraz" wyglądałby na poprawny
    dopóki zegar stoi. Przy progu równym końcowi okna szczegóły z pierwszego przebiegu są
    nadal świeże i drugi przebieg kosztuje zero żądań o szczegóły.

    Pięć minut, a nie godzina: skok większy niż dzierżawa blokady (600 s) zapala detektor snu
    maszyny w `LockHeartbeat`, więc test hałasowałby ostrzeżeniem o czymś, czego nie bada."""
    clock = FakeClock(start_wall=TERAZ.timestamp())
    rejestr = Rejestr()
    deps = _deps(tmp_path, clock, rejestr)

    run_update(deps, since=OD, until=DO)
    po_pierwszym = list(rejestr.pytano_o_szczegoly)
    clock.advance(300)
    run_update(deps, since=OD, until=DO)

    assert po_pierwszym, "pierwszy przebieg nie kupił szczegółów, więc powtórka nic nie mierzy"
    assert rejestr.pytano_o_szczegoly == po_pierwszym
    deps.store.close()


# ------------------------------------------------------------------------ zakres i jego koniec


def test_zakres_bez_argumentow_bierze_teraz_z_wstrzyknietego_zegara(tmp_path: Path) -> None:
    """„Teraz" ma pochodzić z zegara `Deps`, inaczej ten sam przebieg dwa razy znaczy co innego.

    Dopóki `update_range` wołał `datetime.now()`, była to jedyna wielkość w ścieżce
    `aktualizuj`, której test ani demo nie mogły ustawić — a od niej zależy i zakres zmian,
    i próg świeżości szczegółów."""
    deps = _deps(tmp_path, FakeClock(start_wall=TERAZ.timestamp()), Rejestr())

    od, do = update_range(deps)

    assert (od, do) == (TERAZ - timedelta(days=1), TERAZ)
    deps.store.close()


def test_zakres_od_znacznika_konczy_sie_na_zegarze(tmp_path: Path) -> None:
    """Znacznik daje początek, zegar koniec — tabela kosztów i pobranie liczą ten sam zakres."""
    deps = _deps(tmp_path, FakeClock(start_wall=TERAZ.timestamp()), Rejestr())
    deps.store.set_watermark("zmiana:test", utc_iso(OD.timestamp()))

    assert update_range(deps) == (OD, TERAZ)
    deps.store.close()


def test_koniec_zakresu_z_przyszlosci_jest_odrzucany_takze_z_pominieciem_cli(
    tmp_path: Path,
) -> None:
    """Drugie wejście też ma strażnika, i to odmawiającego — nie przycinającego.

    Wejścia są dwa: polecenie i kreator (`wizard.handle_update` również przyjmuje `until`),
    więc strażnik wyłącznie w `cli` zostawiałby kreator bez obrony. To ta sama celowa
    dwuwarstwowość, co `client._checked_host` obok `AllowedHostsTransport` (reguła 11).

    Odmowa, nie przycięcie: przycięcie zamieniłoby `--od jutro --do pojutrze` w pusty zakres
    i komunikat „nic się nie zmieniło", czyli odpowiedź na pytanie, którego nikt nie zadał."""
    deps = _deps(tmp_path, FakeClock(start_wall=TERAZ.timestamp()), Rejestr())
    przyszlosc = TERAZ + timedelta(days=2)

    with pytest.raises(ConfigError):
        update_range(deps, since=OD, until=przyszlosc)
    # Przeszłość przechodzi nietknięta — strażnik nie ma prawa niczego dorzucić od siebie.
    assert update_range(deps, since=OD, until=DO) == (OD, DO)
    deps.store.close()


def test_koniec_zakresu_z_przyszlosci_jest_odrzucany() -> None:
    """`--do` w przyszłości psuje dwie rzeczy naraz i obie po cichu.

    Znacznik zapisany na przyszłość kazałby **następnemu** przebiegowi pominąć wszystko, co
    zmieni się w międzyczasie, a próg świeżości z przyszłości znaczyłby „szczegół musi
    pochodzić z przyszłości" — czyli każdy przebieg kupowałby wszystko od nowa."""
    jutro = (datetime.now(tz=UTC) + timedelta(days=1)).date().isoformat()

    with pytest.raises(ConfigError) as exc:
        _koniec_zakresu_zmian(jutro, SystemClock())

    assert jutro in str(exc.value)


def test_koniec_zakresu_na_dzis_przechodzi() -> None:
    """Granica po stronie operatora: „do dzisiaj" to normalne zapytanie, nie próba wróżenia.

    Data bez godziny znaczy północ, więc dzisiejsza data jest przeszłością przez cały dzień.
    Gdyby strażnik odrzucał również dziś, `--do` byłoby flagą, której nie da się użyć."""
    dzis = datetime.now(tz=UTC).date()

    koniec = _koniec_zakresu_zmian(dzis.isoformat(), SystemClock())

    assert koniec == datetime(dzis.year, dzis.month, dzis.day, tzinfo=UTC)


def test_koniec_zakresu_ze_strefa_jest_przeliczany_na_utc() -> None:
    """Cały program liczy w UTC; data ze strefą ma się przeliczyć, a nie zgubić przesunięcie.

    Naiwny `datetime` w porównaniu ze świadomym strefy podnosi `TypeError`, więc obie flagi
    idą przez jeden konwerter — a ten test pilnuje, że konwersja jest przeliczeniem."""
    # Zegar podany wprost, żeby test nie zależał od dnia, w którym biegnie.
    zegar = FakeClock(start_wall=datetime(2026, 9, 30, tzinfo=UTC).timestamp())
    assert _koniec_zakresu_zmian("2026-09-05T12:00:00+02:00", zegar) == datetime(
        2026, 9, 5, 10, tzinfo=UTC
    )


def test_brak_konca_zakresu_zostaje_brakiem() -> None:
    """Bez `--do` zakres domyka „teraz" z zegara — strażnik nie ma prawa wymyślić granicy."""
    assert _koniec_zakresu_zmian(None, SystemClock()) is None


# ------------------------------------------------------------------------------ linia poleceń


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "read_token_from_keyring", lambda *_: None)
    # Szeroka konsola i brak koloru, bo asercje dotyczą **treści** komunikatu. `rich` dobiera
    # szerokość z terminala i koloruje w środku napisu, więc bez tego test mierzyłby maszynę,
    # na której akurat biegnie — pierwszy przebieg CI (2026-09-08) wywrócił na tym pięć testów.
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setattr(cli.console, "width", 200)
    return CliRunner()


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return {"CEIDG_TOKEN": "token-testowy-nie-jwt", "CEIDG_DATA_DIR": str(tmp_path / "dane")}


def _bez_zmian(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    """Transport, który na każde pytanie o liczbę zmian odpowiada „zero" i zapisuje adres."""
    api = FakeApi()
    pusto = {"count": 0, "identyfikatoryWpisow": []}
    api.fallback = lambda request: httpx.Response(200, json=pusto)

    def fake(settings: Settings, *, events: Events | None = None, **_: object) -> Deps:
        # Zegar na `TERAZ`, a nie domyślny z `conftest` (2023-11-14): daty w tych testach
        # są z 2026, a strażnik `--do` porównuje właśnie z zegarem zależności.
        return build_deps(
            settings,
            events=events,
            http=api.client(),
            clock=FakeClock(start_wall=TERAZ.timestamp()),
        )

    monkeypatch.setattr("ceidg_tool.cli.build_deps", fake)
    return api


def test_aktualizuj_reklamuje_flage_konca_zakresu(runner: CliRunner) -> None:
    """Flaga, której nie ma w pomocy, nie istnieje dla operatora."""
    result = runner.invoke(app, ["aktualizuj", "--help"])

    assert "--do" in result.output


def test_koniec_zakresu_z_przyszlosci_konczy_polecenie_zanim_poleci_zadanie(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Odmowa przed pierwszym żądaniem, bo to jest cała jej wartość.

    Gdyby strażnik stał za wyceną, operator zapłaciłby żądaniem za zakres, który i tak zostanie
    odrzucony — a przy pustej odpowiedzi zobaczyłby „nic się nie zmieniło" zamiast powodu."""
    api = _bez_zmian(monkeypatch)
    jutro = (datetime.now(tz=UTC) + timedelta(days=1)).date().isoformat()

    result = runner.invoke(app, ["aktualizuj", "--do", jutro, "--tak"], env=env)

    assert result.exit_code == 3, result.output
    assert "przyszłość" in result.output
    assert api.requests == [], "odmowa zapadła dopiero po żądaniu do rejestru"


def test_koniec_zakresu_z_przeszlosci_domyka_zakres_zadania(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--do` ma dojść do rejestru, bo po to jest: pozwala wziąć dwie godziny zamiast wszystkiego.

    Bez tej flagi jedynym końcem zakresu jest „teraz", więc `aktualizuj` przy rozjechanym
    znaczniku jest zobowiązaniem na czterdzieści minut, którego nie da się przyciąć. Asercja
    dotyczy parametru w żądaniu, a nie tego, co wypisał ekran."""
    api = _bez_zmian(monkeypatch)

    result = runner.invoke(
        app, ["aktualizuj", "--od", "2026-09-04", "--do", "2026-09-05", "--tak"], env=env
    )

    assert result.exit_code == 0, result.output
    assert api.requests, "polecenie nie zapytało nawet o liczbę zmian"
    assert httpx.URL(api.requests[0]).params["datado"] == "2026-09-05 00:00:00"


def test_koniec_zakresu_ktory_nie_jest_data_mowi_o_formacie(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zła data to pomyłka operatora, więc komunikat ma nazwać flagę i oczekiwany format."""
    _bez_zmian(monkeypatch)

    result = runner.invoke(app, ["aktualizuj", "--do", "wczoraj", "--tak"], env=env)

    assert result.exit_code == 3, result.output
    assert "--do musi być datą ISO" in result.output


# ---------------------------------------------------------------------- zdanie dla operatora


def test_podsumowanie_bez_wpisow_sprzed_zmiany_brzmi_jak_dotad() -> None:
    """Zero to norma — uwaga „0 wpisów zachowało stary opis" byłaby szumem przy każdym runie."""
    assert texts.update_summary(2891, 2891, 0, 0) == "Zmienionych wpisów: 2891, szczegółów: 2891."
    assert texts.update_summary(2891, 2891) == texts.update_summary(2891, 2891, 0, 0)


def test_podsumowanie_liczy_wpisy_ktore_zostaly_z_opisem_sprzed_zmiany() -> None:
    """742 z 2 891 — liczba z bazy operatora, której podsumowanie nie miało jak wypowiedzieć.

    Porównanie z wynikiem funkcji, nie z literałem: zdanie stoi w jednym miejscu i to ono jest
    źródłem dla polecenia i dla kreatora (reguła 9)."""
    zdanie = texts.update_summary(2891, 2149, 0, 742)

    assert zdanie.startswith("Zmienionych wpisów: 2891, szczegółów: 2149.")
    assert zdanie.endswith(texts.stale_details_note(742))
    assert "742" in texts.stale_details_note(742)


def test_stary_opis_i_brak_szczegolow_to_dwa_rozne_zdania() -> None:
    """Inna awaria, inna rada — i obie mogą wystąpić w jednym przebiegu.

    `unresolved` mówi „wpis nie ma szczegółów wcale", `stale_details` mówi „ma, tylko sprzed
    zmiany". Jedno zdanie na oba przypadki kazałoby operatorowi zgadywać, co się stało."""
    zdanie = texts.update_summary(2891, 2149, 3, 742)

    assert texts.stale_details_note(742) != texts.unresolved_note(742)
    assert texts.unresolved_note(3) in zdanie
    assert texts.stale_details_note(742) in zdanie


# ------------------------------------------ pusty zakres na każdej z trzech gałęzi początku


def test_zakres_od_i_do_w_tym_samym_dniu_jest_odmowa(tmp_path: Path) -> None:
    """`--od 2026-09-05 --do 2026-09-05` to naturalny zapis „zmiany z 5 września".

    Obie flagi biorą daty, a data znaczy północ, więc taki zakres jest pusty. Bez kontroli
    `update_windows` zwracało pustą listę, `plan.count` wychodził zerem i operator czytał
    „Baza jest aktualna" — odpowiedź, jakby rejestr został zapytany, choć nie poszło ani
    jedno żądanie. Przypadek stał się osiągalny dopiero z flagą `--do`."""
    deps = _deps(tmp_path, FakeClock(start_wall=TERAZ.timestamp()), Rejestr())
    dzien = datetime(2026, 9, 5, tzinfo=UTC)

    with pytest.raises(ConfigError) as exc:
        update_range(deps, since=dzien, until=dzien)

    assert "pusty" in str(exc.value)
    deps.store.close()


def test_znacznik_pozniejszy_niz_podany_koniec_jest_odmowa(tmp_path: Path) -> None:
    """Gałąź ze znacznikiem jest tą, w której operator nie widzi, co mu powiedziano.

    Początek zakresu pochodzi wtedy z bazy, a nie z flagi, więc `aktualizuj --do <wczoraj>`
    przy znaczniku na dziś dawało pusty podział i „Baza jest aktualna" — mimo że operator
    poprosił o konkretny, wcześniejszy zakres. Kontrola tylko w gałęzi `since is not None`
    tego nie łapała."""
    clock = FakeClock(start_wall=TERAZ.timestamp())
    deps = _deps(tmp_path, clock, Rejestr())
    deps.store.set_watermark(update_scope(deps), "2026-09-08T00:00:00Z")

    with pytest.raises(ConfigError) as exc:
        update_range(deps, until=datetime(2026, 9, 5, tzinfo=UTC))

    assert "pusty" in str(exc.value)
    deps.store.close()


def test_znacznik_zmian_nie_cofa_sie(tmp_path: Path) -> None:
    """Nadrobienie starszego zakresu nie ma kasować postępu.

    Z flagą `--do` da się świadomie pobrać starszy zakres. Bezwarunkowy zapis cofałby wtedy
    znacznik, a następny przebieg kupowałby różnicę drugi raz — danych to nie traci, ale
    jednostką rachunku są tu czterdziestominutowe przebiegi. Do czasu wprowadzenia `--do`
    każda ścieżka kończyła zakres na „teraz", więc monotoniczność brała się sama z siebie."""
    clock = FakeClock(start_wall=TERAZ.timestamp())
    with _magazyn(tmp_path, clock) as store:
        store.set_watermark("zmiana:test", "2026-09-08T00:00:00Z")
        store.set_watermark("zmiana:test", "2026-09-05T00:00:00Z")

        assert store.get_watermark("zmiana:test") == "2026-09-08T00:00:00Z"


def test_wpisy_ze_starym_opisem_zostawiaja_slad_poza_ekranem(tmp_path: Path) -> None:
    """Alarm ma przeżyć sesję, a nie tylko przewinąć się po terminalu.

    Po czterdziestu minutach bez widza podsumowanie żyje wyłącznie w przewijaniu terminala.
    Ten projekt zamknął już jeden defekt tego kształtu: dziesięć godzin czekania nie zostawiło
    śladu, bo `on_wait` docierał wyłącznie na ekran. `on_message` idzie przez `_LogEvents`,
    więc trafia do pliku, który przeżywa proces."""
    komunikaty: list[str] = []

    class Zbierak(NullEvents):
        def on_message(self, text: str) -> None:
            komunikaty.append(text)

    # Zegar startuje **wewnątrz** okna zmian, żeby zapisany szczegół był starszy niż jego
    # koniec — dopiero taki wpis jest tym, o który chodzi. Potem zegar przechodzi za okno,
    # bo tylko wtedy `update_range` w ogóle przyjmie ten zakres.
    clock = FakeClock(start_wall=datetime(2026, 9, 7, 12, tzinfo=UTC).timestamp())
    api = FakeApi()
    rejestr = Rejestr()
    api.fallback = rejestr.handler
    settings = Settings(token="tok", environment="test", data_dir=tmp_path / "dane")
    deps = build_deps(settings, clock=clock, http=api.client(), events=Zbierak())
    deps.store.save_details(details=[{**detail_record(1), "id": WPIS, "nazwa": PRZED_ZMIANA}])
    clock.advance(int((TERAZ - datetime(2026, 9, 7, 12, tzinfo=UTC)).total_seconds()))
    # Filtr świeżości zepsuty do zachowania sprzed naprawy: wpis zostaje ze starym opisem,
    # więc obserwator ma o tym powiedzieć — i to do logu, nie tylko na ekran.
    deps.store.stale_detail_ids = lambda ids, *, cutoff: []  # type: ignore[method-assign]

    run_update(deps, since=OD, until=DO)

    assert any("sprzed" in k for k in komunikaty), komunikaty
    deps.store.close()
