"""`aktualizuj` od `/zmiana` do bazy — czy run niesie dane, czy zaślepki (ADR-0013).

Produkcyjny objaw był jednoznaczny i żaden test go nie widział: nocny przebieg z 2026-09-08
kupił 13 401 rekordów za 2 681 żądań i pokazał **zero**. Rejestr oddaje ten sam identyfikator
wpisu małymi literami z `/zmiana` i wielkimi z `/firma`, a `firma.id` był kluczem głównym
wrażliwym na wielkość liter — więc każda zmieniona firma lądowała w bazie dwa razy: zaślepka
bez danych przypięta do runu i komplet szczegółów przypięty do niczego. Stąd 31 860 wierszy
na 16 310 firm i `stale_detail_ids`, który nie trafił w cache ani razu.

Testy w tym pliku idą całą ścieżką (`run_update` → klient → magazyn) i pytają o objaw wprost:
czy rekordy runu mają szczegóły, ile wierszy stoi w bazie na jedną firmę i czy druga
aktualizacja tego samego okna kosztuje jeszcze jedno żądanie. Atrapa `/firma` **musi**
odpowiadać pisownią rejestru (`support.registry_id`) — atrapa odbijająca pisownię z zapytania
zgadza się sama ze sobą i z definicji nie może tego defektu pokazać.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from ceidg_tool.config import Settings
from ceidg_tool.pipeline import Deps, LockLostError, RunResult, build_deps, run_update
from ceidg_tool.recordid import GUID_WPISU
from tests.conftest import FakeClock, detail_record
from tests.support import FakeApi, registry_id

BATCH = 5
OKNO = (datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 3, tzinfo=UTC))
ZMIENIONE = tuple(f"18578BAF-BAC7-42B9-AA7E-4C3666132E{i:02X}" for i in range(10))


class Rejestr:
    """Atrapa API w pisowni, którą rejestr naprawdę zwraca.

    Dwie własności są tu zmierzone, nie wymyślone (`tests/fixtures/api_traits.yaml`):
    `/zmiana` oddaje identyfikatory małymi literami, a `/firma` — wielkimi, niezależnie od
    tego, jaką pisownią go zapytano, bo `ids=` dopasowuje bez względu na wielkość liter."""

    def __init__(self, ids: Sequence[str], *, nieznane: Collection[str] = ()) -> None:
        self.ids = [rid.upper() for rid in ids]
        self.nieznane = {rid.upper() for rid in nieznane}
        self.zadania: dict[str, int] = {"zmiana": 0, "firma": 0}
        self.pytano_o: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/zmiana" in url:
            self.zadania["zmiana"] += 1
            adres = f"https://test-dane.biznes.gov.pl/api/ceidg/v3/zmiana?strona={url[-1]}"
            return httpx.Response(
                200,
                json={
                    # Małymi — to jest ta jedna litera różnicy, przez którą wywrócił się
                    # cały tryb aktualizacji.
                    "identyfikatoryWpisow": [rid.lower() for rid in self.ids],
                    "count": len(self.ids),
                    # `next == self` kończy paginację po jednej stronie (`client._iter_paged`).
                    "links": {"self": adres, "next": adres},
                },
            )
        if "/firma" in url:
            self.zadania["firma"] += 1
            pytane = request.url.params.get_list("ids")
            self.pytano_o.extend(pytane)
            znane = [rid for rid in pytane if rid.upper() not in self.nieznane]
            return httpx.Response(
                200,
                json={"firma": [{**detail_record(1), "id": registry_id(rid)} for rid in znane]},
            )
        return httpx.Response(404, json={"code": "BRAK", "message": url})


def _deps(tmp_path: Path, clock: FakeClock, rejestr: Rejestr) -> Deps:
    api = FakeApi()
    api.fallback = rejestr.handler
    settings = Settings(token="tok", environment="test", data_dir=tmp_path / "dane")
    deps = build_deps(settings, clock=clock, http=api.client())
    deps.profile = deps.profile.model_copy(
        update={"ids_batch_size": BATCH, "max_limit_zmiana": len(ZMIENIONE)}
    )
    assert deps.client is not None
    deps.client._profile = deps.profile
    return deps


def _aktualizuj(deps: Deps) -> RunResult:
    since, until = OKNO
    return run_update(deps, since=since, until=until)


def _wierszy_firma(deps: Deps) -> int:
    return int(deps.store._conn.execute("SELECT COUNT(*) FROM firma").fetchone()[0])


@pytest.fixture
def rejestr() -> Rejestr:
    return Rejestr(ZMIENIONE)


def test_rekordy_runu_zmiany_niosa_szczegoly_a_nie_stan_brak(
    tmp_path: Path, clock: FakeClock, rejestr: Rejestr
) -> None:
    """Objaw produkcyjny wprost: 13 401 rekordów, z których żaden nie miał danych.

    `detail_state == 'brak'` znaczyło tyle, że eksport zobaczy pusty skoroszyt mimo 2 681
    opłaconych żądań — więc asercja jest o stanie każdego rekordu, nie o ich liczbie."""
    deps = _deps(tmp_path, clock, rejestr)
    wynik = _aktualizuj(deps)

    rekordy = list(deps.store.iter_run_records(wynik.run_id))
    assert len(rekordy) == len(ZMIENIONE)
    assert [r.detail_state for r in rekordy] == ["pobrany"] * len(ZMIENIONE)
    assert all(r.detail_json is not None for r in rekordy)
    assert wynik.details == wynik.records == len(ZMIENIONE)
    assert wynik.unresolved == 0
    deps.store.close()


def test_kazda_firma_zajmuje_w_bazie_jeden_wiersz(
    tmp_path: Path, clock: FakeClock, rejestr: Rejestr
) -> None:
    """31 860 wierszy na 16 310 firm — po poprawce wierszy jest dokładnie tyle, co firm."""
    deps = _deps(tmp_path, clock, rejestr)
    wynik = _aktualizuj(deps)

    assert _wierszy_firma(deps) == len(ZMIENIONE)
    ids = [r.id for r in deps.store.iter_run_records(wynik.run_id)]
    assert sorted(ids) == sorted(ZMIENIONE)
    deps.store.close()


def test_identyfikatory_w_bazie_sa_w_postaci_kanonicznej(
    tmp_path: Path, clock: FakeClock, rejestr: Rejestr
) -> None:
    """Baza ma jedną pisownię, tę którą rejestr sam produkuje na endpointach z danymi.

    Gdyby zapis szedł pisownią z `/zmiana`, kolejne pobranie po `/firmy` dorobiłoby drugi
    wiersz — defekt wróciłby drugą stroną, bez żadnego `aktualizuj`."""
    deps = _deps(tmp_path, clock, rejestr)
    _aktualizuj(deps)

    zapisane = [str(r[0]) for r in deps.store._conn.execute("SELECT id FROM firma")]
    assert zapisane, "run nie zapisał ani jednego wiersza"
    for rid in zapisane:
        assert GUID_WPISU.fullmatch(rid) is not None
        assert rid == rid.upper()
    deps.store.close()


def test_druga_aktualizacja_tego_samego_okna_nie_kupuje_szczegolow(
    tmp_path: Path, clock: FakeClock, rejestr: Rejestr
) -> None:
    """`stale_detail_ids` nie trafiał w cache ani razu — każdy przebieg płacił od nowa.

    To była druga połowa rachunku: nie tylko pusty wynik, ale i pełna cena za dane, które
    już leżały w bazie pod inną pisownią."""
    deps = _deps(tmp_path, clock, rejestr)
    _aktualizuj(deps)
    po_pierwszym = rejestr.zadania["firma"]
    assert po_pierwszym == len(ZMIENIONE) // BATCH

    _aktualizuj(deps)
    assert rejestr.zadania["firma"] == po_pierwszym, "szczegóły kupione drugi raz mimo cache"
    assert _wierszy_firma(deps) == len(ZMIENIONE)
    deps.store.close()


def test_brak_jednego_wpisu_nie_zabiera_szczegolow_pozostalym(
    tmp_path: Path, clock: FakeClock
) -> None:
    """„Nie ma tego rekordu" ma znaczyć dokładnie tyle, ile mówi — i nic ponadto.

    Przed poprawką `fetch_details` porównywał pisownie przez `.upper()` — jedyne takie
    miejsce w programie — więc lista `missing` wychodziła pusta zawsze. Po poprawce
    porównanie jest równością, więc brakujący wpis naprawdę zostaje w `missing`; ta asercja
    pilnuje, że rachunek się zgadza: jeden bez szczegółów, dziewięć ze szczegółami."""
    brakujacy = ZMIENIONE[3]
    rejestr = Rejestr(ZMIENIONE, nieznane=[brakujacy])
    deps = _deps(tmp_path, clock, rejestr)
    wynik = _aktualizuj(deps)

    rekordy = {r.id: r for r in deps.store.iter_run_records(wynik.run_id)}
    assert rekordy[brakujacy].detail_json is None
    assert all(r.detail_json is not None for rid, r in rekordy.items() if rid != brakujacy)
    assert wynik.details == len(ZMIENIONE) - 1
    deps.store.close()


def test_wpis_nieznany_rejestrowi_dostaje_stan_nieznaleziony(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Znaleziony przy pisaniu tych testów jako `xfail`, naprawiony tego samego dnia.

    W `run_update` kolejność była odwrotna niż w `_fetch_details`: najpierw `save_details`,
    potem `link_ids`. W trybie `/zmiana` wiersz `firma` powstaje dopiero w `link_ids`, więc
    `UPDATE firma SET detail_state = 'nieznaleziony'` z `save_details` nie miał czego zmienić
    i po cichu przepadał. Skutek był policzalny: wpis zostawał w stanie `brak`, czyli wracał
    do `pending_detail_ids` przy każdym następnym przebiegu i był kupowany od nowa —
    dokładnie ta klasa cichego kosztu, którą ADR-0013 zamyka od strony pisowni.

    `link_ids` biegnie teraz **przed** pobraniem szczegółów, więc wiersz istnieje, zanim
    ktokolwiek próbuje go zaktualizować."""
    brakujacy = ZMIENIONE[3]
    rejestr = Rejestr(ZMIENIONE, nieznane=[brakujacy])
    deps = _deps(tmp_path, clock, rejestr)
    wynik = _aktualizuj(deps)

    stany = {r.id: r.detail_state for r in deps.store.iter_run_records(wynik.run_id)}
    try:
        assert stany[brakujacy] == "nieznaleziony"
    finally:
        deps.store.close()


def test_licznik_niewyjasnionych_jest_zerem_takze_gdy_wpisu_brak(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Alarm ADR-0013: ile wpisów runu skończyło jako praca wykonana i zgubiona.

    Po `aktualizuj` każdy zmieniony identyfikator ma skończyć jako `pobrany`, `nieznaleziony`
    albo `blad`; stan `brak` znaczy, że żądanie poszło, a wynik nie ma gdzie usiąść. Nocny
    przebieg z 13 401 wpisami miał ich 13 401, a podsumowanie mówiło tylko „szczegółów: 0",
    bez niczego do porównania. Asercja jest o obu stronach naraz: brak wpisu w rejestrze to
    **wyjaśnienie**, a nie zguba, więc licznik ma zostać zerem także wtedy."""
    brakujacy = ZMIENIONE[3]
    rejestr = Rejestr(ZMIENIONE, nieznane=[brakujacy])
    deps = _deps(tmp_path, clock, rejestr)
    wynik = _aktualizuj(deps)

    assert wynik.unresolved == 0
    assert wynik.details == len(ZMIENIONE) - 1
    stany = {r.detail_state for r in deps.store.iter_run_records(wynik.run_id)}
    assert stany == {"pobrany", "nieznaleziony"}
    deps.store.close()


def test_utrata_blokady_w_trakcie_konczy_run_jako_przerwany(
    tmp_path: Path, clock: FakeClock, rejestr: Rejestr, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blokadę przejmuje żywy proces w środku pracy — run ma stanąć, a nie pisać dalej.

    Dwa procesy na jednym runie nie zdublują rekordów (klucz główny), ale oba zapisują
    checkpoint i oba oznaczają run jako zakończony, więc eksport z tego okna bywa niepełny.
    `LockLostError` jest `ResumableError`, więc run kończy się jako `przerwany`, a znacznik
    zmian **nie** przesuwa się na okno, którego nie domknięto — inaczej następny `aktualizuj`
    przeskoczyłby dziurę i nikt by się o niej nie dowiedział."""
    deps = _deps(tmp_path, clock, rejestr)
    prawdziwy = deps.store.touch_lock
    bicia = {"n": 0}

    def kradziez() -> bool:
        bicia["n"] += 1
        if bicia["n"] < 2:
            return prawdziwy()
        with deps.store._conn:
            deps.store._conn.execute(
                "INSERT INTO run_lock(environment, pid, started_utc, heartbeat_epoch) "
                "VALUES ('test', ?, '2026-09-08T02:00:00Z', ?) "
                "ON CONFLICT(environment) DO UPDATE SET pid = excluded.pid, "
                "heartbeat_epoch = excluded.heartbeat_epoch",
                (os.getpid() + 1, deps.clock.wall()),
            )
        return False

    monkeypatch.setattr(deps.store, "touch_lock", kradziez)

    with pytest.raises(LockLostError):
        _aktualizuj(deps)

    run = deps.store.list_runs()[0]
    assert run.status == "przerwany"
    assert run.error and "inny proces" in run.error.lower()
    assert deps.store.get_watermark("zmiana:test") is None
    deps.store.close()


def test_znacznik_zmian_przesuwa_sie_po_udanym_oknie(
    tmp_path: Path, clock: FakeClock, rejestr: Rejestr
) -> None:
    """Domknięcie ścieżki: run kończy się zapisanym postępem, a nie samym zapisem rekordów."""
    deps = _deps(tmp_path, clock, rejestr)
    wynik = _aktualizuj(deps)

    assert wynik.status == "zakonczony"
    assert deps.store.get_watermark("zmiana:test") == "2026-09-03T00:00:00Z"
    deps.store.close()


def test_przerwanie_w_srodku_strony_zostawia_w_cache_to_co_pobrano(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Zapis po każdej porcji, nie raz na stronę — inaczej przerwanie kosztuje całą stronę.

    Strona `/zmiana` to do 500 identyfikatorów, czyli do stu żądań i ponad sześć minut pracy.
    Dopóki `save_details` szedł raz na stronę, `Ctrl+C` albo `LockLostError` w środku strony
    wyrzucały ten cały wysiłek do kosza i **nie odkładały go w cache**, więc powtórka kupowała
    te same rekordy jeszcze raz. Test przerywa po pierwszej porcji i pyta o dwie rzeczy: co
    zostało w bazie i ile żądań kosztuje powtórka."""
    rejestr = Rejestr(ZMIENIONE)
    deps = _deps(tmp_path, clock, rejestr)
    granica = BATCH  # przerywamy zaraz po pierwszej porcji

    prawdziwy_fetch = rejestr.handler

    def przerywany(request: httpx.Request) -> httpx.Response:
        if "/firma" in str(request.url) and rejestr.zadania["firma"] >= 1:
            raise KeyboardInterrupt("przerwane przez użytkownika")
        return prawdziwy_fetch(request)

    api = FakeApi()
    api.fallback = przerywany
    deps.client._http = api.client()  # type: ignore[union-attr]

    with pytest.raises(KeyboardInterrupt):
        _aktualizuj(deps)

    pobrane = deps.store._conn.execute(
        "SELECT COUNT(*) FROM firma WHERE detail_state = 'pobrany'"
    ).fetchone()[0]
    assert int(pobrane) == granica, "porcja opłacona żądaniami nie trafiła do cache"

    # Powtórka: to, co już kupiono, ma się nie kupować drugi raz.
    rejestr2 = Rejestr(ZMIENIONE)
    api2 = FakeApi()
    api2.fallback = rejestr2.handler
    deps.client._http = api2.client()  # type: ignore[union-attr]
    _aktualizuj(deps)

    pozostale = len(ZMIENIONE) - granica
    assert rejestr2.zadania["firma"] == pozostale // BATCH
    deps.store.close()
