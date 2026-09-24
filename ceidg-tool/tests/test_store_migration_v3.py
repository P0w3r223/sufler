"""Migracja v2 → v3: dwie tożsamości jednego wpisu zlewane w jedną (ADR-0013).

Baza operatora z 2026-09-08 miała 31 860 wierszy `firma` na 16 310 rzeczywistych firm:
`aktualizuj` zapisywał każdą zmienioną firmę dwa razy — zaślepkę bez danych, przypiętą do
runu pod pisownią z `/zmiana`, i komplet szczegółów pod pisownią z `/firma`, przypięty do
niczego. Sama poprawka kanonizacji nie leczy takiej bazy: zaślepki dalej byłyby jedynym,
co widzi eksport, a osierocone szczegóły zniknęłyby przy najbliższym `wyczysc`, bo
`purge_older_than` kasuje dokładnie wiersze, których nie trzyma żaden run.

Testy budują bazę v2 wprost po SQL — w kształcie, jaki naprawdę zostawiła produkcja — i
pytają o skutki, a nie o przebieg migracji: czy run niesie dane, czy liczniki mówią prawdę,
czy scalony wpis przeżywa czyszczenie, co się dzieje, gdy tę samą bazę migrują dwa procesy
naraz, i czy powtórzenie całej migracji na już zmigrowanej bazie jest bezpieczne. Te dwa
ostatnie są tu dlatego, że ich objawem nie jest błąd, tylko pusta kolumna: `integrity_check`
zostaje czysty, oba przebiegi kończą się sukcesem, a wartość znika.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ceidg_tool.clock import utc_iso
from ceidg_tool.recordid import kanoniczne_id
from ceidg_tool.store import _SCHEMA, SCHEMA_VERSION, Store
from tests.conftest import FakeClock

# Ten sam wpis w obu pisowniach, w których rejestr go zwraca.
UPPER = "18578BAF-BAC7-42B9-AA7E-4C3666132E69"
LOWER = UPPER.lower()
UPPER_2 = "AA448259-5001-4B7B-AD40-67F450032B35"
LOWER_2 = UPPER_2.lower()
# Trzecia pisownia tego samego wpisu — nie do wyprodukowania przez rejestr, ale
# kanonizująca się do `UPPER`. Służy do zbudowania grupy **bez** wiersza kanonicznego.
MIXED = "18578Baf-BaC7-42b9-Aa7E-4c3666132E69"

SZCZEGOLY = {"id": UPPER, "nazwa": "Firma Testowa", "status": "AKTYWNY"}
LISTA = {"id": UPPER, "nazwa": "Firma Testowa", "status": "AKTYWNY"}


def _teraz(clock: FakeClock) -> str:
    """Znaczniki czasu idą z zegara testu, nie z kalendarza.

    Wpisana data byłaby prawdziwa tylko do najbliższego progu TTL: `stale_detail_ids`
    i `purge_older_than` liczą od `clock.wall()`, więc „2026-09-08" przy zegarze stojącym
    w 2023 opisywałoby dane z przyszłości i czyniło asercje o świeżości bezprzedmiotowymi."""
    return utc_iso(clock.wall())


def _dni_temu(clock: FakeClock, dni: float) -> str:
    return utc_iso(clock.wall() - dni * 86_400)


def _baza_v2(path: Path, clock: FakeClock) -> sqlite3.Connection:
    """Pusta baza w wersji 2 schematu.

    v2 i v3 różnią się **danymi**, nie DDL — migracja `_upgrade_to_3` nie dokłada ani jednej
    kolumny — więc tabele budujemy tym samym skryptem, którego używa produkcja. Przepisanie
    ich ręcznie dawałoby test, który z czasem opisuje nieistniejący schemat."""
    conn = sqlite3.connect(path)
    # WAL, bo w takim trybie `Store.__init__` zostawia każdą bazę, jaką narzędzie stworzyło —
    # baza v2 w rękach operatora jest w WAL na pewno. Bez tego atrapa różni się od produkcji
    # w miejscu, które nie ma nic wspólnego z migracją: przełączenie na WAL wymaga blokady
    # wyłącznej i nie respektuje `busy_timeout`, więc drugie równoległe otwarcie dostawało
    # `database is locked` z `__init__`, zanim zdążyło dojść do scalania.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(_SCHEMA)
    conn.execute(f"PRAGMA user_version = {2}")
    _wstaw_run(conn, "r1", clock)
    conn.commit()
    return conn


def _wstaw_run(
    conn: sqlite3.Connection, run_id: str, clock: FakeClock, *, kind: str = "zmiana"
) -> None:
    teraz = utc_iso(clock.wall())
    conn.execute(
        "INSERT INTO run(run_id, created_utc, updated_utc, environment, tool_version, "
        "profile_hash, criteria_json, criteria_hash, mode, status, pages_done, records_seen, "
        "kind) VALUES (?,?,?,'test','0','p','{}','h','szczegoly','zakonczony',1,1,?)",
        (run_id, teraz, teraz, kind),
    )


def _wstaw_firme(
    conn: sqlite3.Connection,
    rid: str,
    *,
    list_json: dict[str, Any] | None = None,
    list_utc: str | None = None,
    detail_json: dict[str, Any] | None = None,
    detail_utc: str | None = None,
    detail_state: str = "brak",
    nip: str | None = None,
    zrodlo: str = "CEIDG_API",
) -> None:
    conn.execute(
        "INSERT INTO firma(id, environment, list_json, list_utc, detail_json, detail_utc, "
        "detail_state, nip, regon, status_api, wojewodztwo, data_rozpoczecia, zrodlo) "
        "VALUES (?,'test',?,?,?,?,?,?,NULL,NULL,NULL,NULL,?)",
        (
            rid,
            None if list_json is None else json.dumps(list_json, ensure_ascii=False),
            list_utc,
            None if detail_json is None else json.dumps(detail_json, ensure_ascii=False),
            detail_utc,
            detail_state,
            nip,
            zrodlo,
        ),
    )


def _zaslepka(conn: sqlite3.Connection, rid: str) -> None:
    """Wiersz, jaki zostawiał `link_ids`: identyfikator z `/zmiana` i nic poza nim."""
    _wstaw_firme(conn, rid)


def _przypnij(conn: sqlite3.Connection, run_id: str, rid: str, *, position: int = 0) -> None:
    conn.execute(
        "INSERT INTO run_firma(run_id, firma_id, page_index, position) VALUES (?,?,0,?)",
        (run_id, rid, position),
    )


def _stan(conn: sqlite3.Connection) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Zawartość obu tabel — materiał do porównania „przed i po” przy idempotencji."""
    firmy = conn.execute("SELECT * FROM firma ORDER BY id").fetchall()
    linki = conn.execute("SELECT * FROM run_firma ORDER BY run_id, firma_id").fetchall()
    return [tuple(r) for r in firmy], [tuple(r) for r in linki]


def _baza_z_polowkami(sciezka: Path, clock: FakeClock) -> None:
    """Wpis z listą pod jedną pisownią i szczegółami pod drugą — naturalny kształt bazy,
    przez którą przeszło i `pobierz`, i `aktualizuj`.

    Ten kształt jest jedynym, na którym widać wyścig dwóch migracji: scalenie ma tu coś do
    zrobienia w **obie** strony (blok szczegółów i uzupełnienie kolumn listy), więc drugi
    plan zbudowany z tego samego snapshotu miał czym nadpisać wynik pierwszego."""
    conn = _baza_v2(sciezka, clock)
    _wstaw_firme(conn, UPPER, list_json=LISTA, list_utc=_dni_temu(clock, 2))
    _wstaw_firme(
        conn,
        LOWER,
        detail_json=SZCZEGOLY,
        detail_utc=_teraz(clock),
        detail_state="pobrany",
        nip="1234567890",
    )
    _przypnij(conn, "r1", UPPER)
    conn.commit()
    conn.close()


@pytest.fixture
def sciezka(tmp_path: Path) -> Path:
    return tmp_path / "v2.sqlite"


# --------------------------------------------------------------------------- produkcyjny kształt


def test_run_ktory_widzial_same_zaslepki_dostaje_szczegoly(sciezka: Path, clock: FakeClock) -> None:
    """Objaw wprost: przed migracją run niesie pustkę, po migracji — kupione dane.

    To jest ten nocny przebieg z 13 401 rekordami, w którym eksport nie pokazał żadnego."""
    conn = _baza_v2(sciezka, clock)
    _zaslepka(conn, LOWER)
    _wstaw_firme(
        conn,
        UPPER,
        detail_json=SZCZEGOLY,
        detail_utc=_teraz(clock),
        detail_state="pobrany",
        nip="1234567890",
    )
    _przypnij(conn, "r1", LOWER)
    conn.commit()
    conn.close()

    with Store(sciezka, environment="test", clock=clock) as store:
        rekordy = list(store.iter_run_records("r1"))
        assert [r.id for r in rekordy] == [UPPER]
        assert rekordy[0].detail_state == "pobrany"
        assert rekordy[0].detail_json == SZCZEGOLY
        assert store.count_run_details("r1") == store.count_run_records("r1") == 1
        assert (store.merged_duplicates, store.renamed_identifiers) == (1, 0)


def test_po_migracji_zostaje_jeden_wiersz_na_wpis(sciezka: Path, clock: FakeClock) -> None:
    """31 860 wierszy na 16 310 firm — liczba wierszy ma się zrównać z liczbą wpisów."""
    conn = _baza_v2(sciezka, clock)
    for maly, duzy in ((LOWER, UPPER), (LOWER_2, UPPER_2)):
        _zaslepka(conn, maly)
        _wstaw_firme(
            conn, duzy, detail_json={"id": duzy}, detail_utc=_teraz(clock), detail_state="pobrany"
        )
        _przypnij(conn, "r1", maly, position=0 if maly == LOWER else 1)
    conn.commit()
    conn.close()

    with Store(sciezka, environment="test", clock=clock) as store:
        ids = [str(r[0]) for r in store._conn.execute("SELECT id FROM firma ORDER BY id")]
        assert ids == sorted([UPPER, UPPER_2])
        assert (store.merged_duplicates, store.renamed_identifiers) == (2, 0)


def test_wersja_schematu_dochodzi_do_biezacej(sciezka: Path, clock: FakeClock) -> None:
    conn = _baza_v2(sciezka, clock)
    conn.commit()
    conn.close()
    with Store(sciezka, environment="test", clock=clock) as store:
        wersja = int(store._conn.execute("PRAGMA user_version").fetchone()[0])
    assert wersja == SCHEMA_VERSION


# --------------------------------------------------------------------------- scalanie treści


def test_scalony_wiersz_bierze_polowki_z_obu_pisowni(sciezka: Path, clock: FakeClock) -> None:
    """Scalenie skleja braki, a nie wybiera między dwiema prawdami.

    Lista przyszła pod jedną pisownią (run po `/firmy`), szczegóły pod drugą (run po
    `/zmiana`); po migracji jeden wiersz ma obie połowy, bo obie zostały kupione żądaniem."""
    conn = _baza_v2(sciezka, clock)
    _wstaw_firme(conn, LOWER, list_json=LISTA, list_utc=_dni_temu(clock, 2))
    _wstaw_firme(
        conn,
        UPPER,
        detail_json=SZCZEGOLY,
        detail_utc=_teraz(clock),
        detail_state="pobrany",
        nip="1234567890",
    )
    _przypnij(conn, "r1", LOWER)
    conn.commit()
    conn.close()

    with Store(sciezka, environment="test", clock=clock) as store:
        rekord = next(iter(store.iter_run_records("r1")))
        assert rekord.list_json == LISTA and rekord.list_utc == _dni_temu(clock, 2)
        assert rekord.detail_json == SZCZEGOLY and rekord.detail_utc == _teraz(clock)
        nip = store._conn.execute("SELECT nip FROM firma WHERE id = ?", (UPPER,)).fetchone()[0]
        assert nip == "1234567890"


def test_swiezsze_szczegoly_wygrywaja_i_ida_w_calosci(sciezka: Path, clock: FakeClock) -> None:
    """Blok szczegółów pochodzi z jednego pobrania — `json`, `utc` i `state` muszą się zgadzać.

    Sklejenie świeższego JSON-a ze starszym znacznikiem dałoby wiersz, o którym TTL cache'u
    mówi nieprawdę, więc `stale_detail_ids` pomijałby dane, których już nie ma."""
    conn = _baza_v2(sciezka, clock)
    stare = {"id": UPPER, "status": "AKTYWNY"}
    nowe = {"id": UPPER, "status": "WYKRESLONY"}
    _wstaw_firme(
        conn, LOWER, detail_json=stare, detail_utc=_dni_temu(clock, 30), detail_state="pobrany"
    )
    _wstaw_firme(conn, UPPER, detail_json=nowe, detail_utc=_teraz(clock), detail_state="pobrany")
    _przypnij(conn, "r1", LOWER)
    conn.commit()
    conn.close()

    with Store(sciezka, environment="test", clock=clock) as store:
        rekord = next(iter(store.iter_run_records("r1")))
        assert rekord.detail_json == nowe
        assert rekord.detail_utc == _teraz(clock)
        assert rekord.detail_state == "pobrany"


def test_pobrany_wygrywa_z_nowszym_bledem(sciezka: Path, clock: FakeClock) -> None:
    """Świeżość rozstrzyga dopiero po stanie: nowszy `blad` nie kasuje starszych danych."""
    conn = _baza_v2(sciezka, clock)
    _wstaw_firme(
        conn, UPPER, detail_json=SZCZEGOLY, detail_utc=_dni_temu(clock, 30), detail_state="pobrany"
    )
    _wstaw_firme(conn, LOWER, detail_utc=_teraz(clock), detail_state="blad")
    _przypnij(conn, "r1", LOWER)
    conn.commit()
    conn.close()

    with Store(sciezka, environment="test", clock=clock) as store:
        rekord = next(iter(store.iter_run_records("r1")))
        assert rekord.detail_state == "pobrany" and rekord.detail_json == SZCZEGOLY


def test_dane_pod_mala_pisownia_trafiaja_do_wiersza_kanonicznego(
    sciezka: Path, clock: FakeClock
) -> None:
    """Scalanie ma być niezależne od kierunku — pełniejszy wiersz wygrywa, nie „ten wielkimi".

    Orientacja odwrotna do produkcyjnej: wiersz kanoniczny istnieje, ale niesie tylko listę
    (run po `/firmy`), a szczegóły stoją pod pisownią z `/zmiana`. Migracja musi obsłużyć
    **dowolną** bazę v2, w tym zostawioną przez wersję pośrednią — inaczej „wielkie litery"
    przestają być postacią kanoniczną, a stają się regułą wyboru zwycięzcy."""
    conn = _baza_v2(sciezka, clock)
    _wstaw_firme(conn, UPPER, list_json=LISTA, list_utc=_dni_temu(clock, 2))
    _wstaw_firme(
        conn,
        LOWER,
        detail_json=SZCZEGOLY,
        detail_utc=_teraz(clock),
        detail_state="pobrany",
        nip="1234567890",
    )
    _przypnij(conn, "r1", UPPER)
    conn.commit()
    conn.close()

    with Store(sciezka, environment="test", clock=clock) as store:
        rekord = next(iter(store.iter_run_records("r1")))
        assert rekord.id == UPPER
        assert rekord.list_json == LISTA
        assert rekord.detail_json == SZCZEGOLY
        assert rekord.detail_state == "pobrany" and rekord.detail_utc == _teraz(clock)
        nip = store._conn.execute("SELECT nip FROM firma WHERE id = ?", (UPPER,)).fetchone()[0]
        assert nip == "1234567890"


def test_grupa_bez_wiersza_kanonicznego_nie_gubi_kolumn_dawcow(
    sciezka: Path, clock: FakeClock
) -> None:
    """Gałąź, w której wiersz kanoniczny trzeba dopiero zmaterializować — i mimo to scalić.

    Dwie pisownie, żadna kanoniczna: kopia najlepszego wiersza staje się celem, ale reszta
    grupy dalej ma czym uzupełnić braki. Wcześniejsze `continue` kończyło tu pracę zaraz po
    kopii, więc jedyna gałąź tej migracji **kasowała** zamiast scalać — a komunikat dla
    operatora obiecuje, że nic nie ginie. Rejestr emituje tylko dwie pisownie, więc kształt
    jest dziś nieosiągalny z sieci; obietnica obowiązuje mimo to i kosztuje jeden test."""
    conn = _baza_v2(sciezka, clock)
    _wstaw_firme(conn, MIXED, list_json=LISTA, list_utc=_dni_temu(clock, 2), nip="1234567890")
    _wstaw_firme(
        conn, LOWER, detail_json=SZCZEGOLY, detail_utc=_teraz(clock), detail_state="pobrany"
    )
    _przypnij(conn, "r1", LOWER)
    conn.commit()
    conn.close()

    with Store(sciezka, environment="test", clock=clock) as store:
        rekord = next(iter(store.iter_run_records("r1")))
        assert rekord.id == UPPER
        assert rekord.detail_json == SZCZEGOLY and rekord.detail_state == "pobrany"
        assert rekord.list_json == LISTA, "kopia najlepszego wiersza zjadła połowę z listy"
        nip = store._conn.execute("SELECT nip FROM firma WHERE id = ?", (UPPER,)).fetchone()[0]
        assert nip == "1234567890", "kopia najlepszego wiersza zjadła skalar dawcy"
        assert (store.merged_duplicates, store.renamed_identifiers) == (1, 0)


def test_zrodlo_idzie_za_danymi_takze_gdy_nie_ma_zadnych_szczegolow(
    sciezka: Path, clock: FakeClock
) -> None:
    """Wiersz może nieść dane i **nie** nieść szczegółów — tak wygląda import z raportu.

    Raport nie ma odpowiednika `/firma`, więc jego wiersze zostają w `detail_state='brak'`
    z wypełnionym `list_json`. Dopóki `zrodlo` jechało przyczepione do bloku szczegółów,
    taki wiersz nie miał czym go przenieść: scalony wpis dostawał `zrodlo` zaślepki, czyli
    'CEIDG_API'. Skutek jest widoczny w skoroszycie, bo `record_sources` steruje ukrywaniem
    kolumn — eksport obiecywał kolumny, których raport nie wypełnia."""
    conn = _baza_v2(sciezka, clock)
    _zaslepka(conn, UPPER)
    _wstaw_firme(
        conn,
        LOWER,
        list_json=LISTA,
        list_utc=_teraz(clock),
        detail_state="brak",
        zrodlo="CEIDG_RAPORT",
    )
    _przypnij(conn, "r1", UPPER)
    conn.commit()
    conn.close()

    with Store(sciezka, environment="test", clock=clock) as store:
        rekord = next(iter(store.iter_run_records("r1")))
        assert rekord.id == UPPER
        assert rekord.list_json == LISTA
        assert rekord.detail_state == "brak"
        assert rekord.zrodlo == "CEIDG_RAPORT", "zrodlo zostalo przy zaslepce"
        assert store.record_sources(["r1"]) == {"CEIDG_RAPORT"}


def test_zrodlo_wedruje_razem_z_blokiem_szczegolow(sciezka: Path, clock: FakeClock) -> None:
    """`zrodlo` opisuje pochodzenie **danych**, więc jedzie z nimi, a nie zostaje z zaślepką.

    Orientacja odwrotna do poprzedniego testu: wiersz kanoniczny jest zaślepką z `link_ids`,
    która zna tylko domyślne 'CEIDG_API', a szczegóły stoją pod pisownią niekanoniczną.
    Gdy `zrodlo` zostawało przy zaślepce, `record_sources` mówiło po migracji 'CEIDG_API' —
    a to ta wartość steruje ukrywaniem kolumn w eksporcie, więc skoroszyt dostawał kolumny,
    których raport nie wypełnia."""
    conn = _baza_v2(sciezka, clock)
    _zaslepka(conn, UPPER)
    _wstaw_firme(
        conn,
        LOWER,
        detail_json=SZCZEGOLY,
        detail_utc=_teraz(clock),
        detail_state="pobrany",
        zrodlo="CEIDG_RAPORT",
    )
    _przypnij(conn, "r1", UPPER)
    conn.commit()
    conn.close()

    with Store(sciezka, environment="test", clock=clock) as store:
        assert store.record_sources(["r1"]) == {"CEIDG_RAPORT"}
        rekord = next(iter(store.iter_run_records("r1")))
        assert rekord.id == UPPER and rekord.zrodlo == "CEIDG_RAPORT"


def test_zrodlo_idzie_z_wiersza_niosacego_dane(sciezka: Path, clock: FakeClock) -> None:
    """`zrodlo` jest NOT NULL i steruje eksportem — zaślepka zna tylko wartość domyślną."""
    conn = _baza_v2(sciezka, clock)
    _zaslepka(conn, LOWER)
    _wstaw_firme(
        conn,
        UPPER,
        detail_json=SZCZEGOLY,
        detail_utc=_teraz(clock),
        detail_state="pobrany",
        zrodlo="CEIDG_RAPORT",
    )
    _przypnij(conn, "r1", LOWER)
    conn.commit()
    conn.close()

    with Store(sciezka, environment="test", clock=clock) as store:
        assert store.record_sources(["r1"]) == {"CEIDG_RAPORT"}


# --------------------------------------------------------------------------- przepięcie runów


def test_jeden_run_ktory_zebral_obie_pisownie(sciezka: Path, clock: FakeClock) -> None:
    """Przypadek, na którym zwykły `UPDATE` by padł: klucz `(run_id, firma_id)` się zderza.

    Zdarza się, gdy ten sam run zobaczył wpis i z `/zmiana`, i ze szczegółów — po migracji
    ma zostać jedno powiązanie, a nie wyjątek `UNIQUE constraint failed`."""
    conn = _baza_v2(sciezka, clock)
    _zaslepka(conn, LOWER)
    _wstaw_firme(
        conn, UPPER, detail_json=SZCZEGOLY, detail_utc=_teraz(clock), detail_state="pobrany"
    )
    _przypnij(conn, "r1", LOWER, position=0)
    _przypnij(conn, "r1", UPPER, position=1)
    conn.commit()
    conn.close()

    with Store(sciezka, environment="test", clock=clock) as store:
        assert store.count_run_records("r1") == 1
        rekord = next(iter(store.iter_run_records("r1")))
        assert rekord.id == UPPER and rekord.detail_json == SZCZEGOLY


def test_dwa_runy_pod_dwiema_pisowniami_zachowuja_oba_powiazania(
    sciezka: Path, clock: FakeClock
) -> None:
    """Scalenie tożsamości nie może scalić runów — każdy dalej widzi swój wpis."""
    conn = _baza_v2(sciezka, clock)
    _wstaw_run(conn, "r2", clock, kind="firmy")
    _zaslepka(conn, LOWER)
    _wstaw_firme(
        conn, UPPER, detail_json=SZCZEGOLY, detail_utc=_teraz(clock), detail_state="pobrany"
    )
    _przypnij(conn, "r1", LOWER)
    _przypnij(conn, "r2", UPPER)
    conn.commit()
    conn.close()

    with Store(sciezka, environment="test", clock=clock) as store:
        assert [r.id for r in store.iter_run_records("r1")] == [UPPER]
        assert [r.id for r in store.iter_run_records("r2")] == [UPPER]


def test_samotna_mala_pisownia_tez_dostaje_postac_kanoniczna(
    sciezka: Path, clock: FakeClock
) -> None:
    """Wpis bez bliźniaka też jest migrowany — inaczej nowy zapis zrobiłby mu drugi wiersz.

    Po poprawce program pisze wyłącznie wielkimi, więc wiersz zostawiony małymi nie zostałby
    już nigdy trafiony: `stale_detail_ids` uznałby go za brak i kupił szczegóły ponownie."""
    conn = _baza_v2(sciezka, clock)
    _wstaw_firme(
        conn, LOWER, detail_json=SZCZEGOLY, detail_utc=_teraz(clock), detail_state="pobrany"
    )
    _przypnij(conn, "r1", LOWER)
    conn.commit()
    conn.close()

    with Store(sciezka, environment="test", clock=clock) as store:
        assert [r.id for r in store.iter_run_records("r1")] == [UPPER]
        # Liczniki są rozdzielone, bo komunikat mówi o wpisach *zapisanych dwukrotnie*:
        # ten wpis miał jeden wiersz i tylko zmienił pisownię, więc liczy się osobno.
        assert (store.merged_duplicates, store.renamed_identifiers) == (0, 1)
        # Cache trafiony po migracji: pytanie o ten wpis (w dowolnej pisowni) nie generuje
        # żądania. Przed migracją wiersz stał pod małą pisownią, a pytanie szło wielką.
        prog = datetime.fromtimestamp(clock.wall() - 86_400, tz=UTC)
        assert store.stale_detail_ids(kanoniczne_id([LOWER]), cutoff=prog) == []


# --------------------------------------------------------------------------- licznik i powtórka


def test_liczniki_rozdzielaja_scalenia_od_przemianowan(sciezka: Path, clock: FakeClock) -> None:
    """Trzy różne losy wpisu w jednej bazie, trzy różne liczby — i tylko dwie są duplikatami.

    Komunikat mówi o wpisach *zapisanych dwukrotnie*, więc wpis, który miał samą starą
    pisownię (przerwany `aktualizuj` zdążył zrobić zaślepkę i nic więcej), nie może wpadać
    do tego samego licznika: zawyżałby liczbę duplikatów tam, gdzie nic się nie zdublowało."""
    conn = _baza_v2(sciezka, clock)
    for maly, duzy in ((LOWER, UPPER), (LOWER_2, UPPER_2)):
        _zaslepka(conn, maly)
        _wstaw_firme(
            conn, duzy, detail_json={"id": duzy}, detail_utc=_teraz(clock), detail_state="pobrany"
        )
    # Sama stara pisownia — przemianowanie, nie scalenie.
    samotny = "b74d0fb1-32e7-4629-8fad-c1a606cb0fb3"
    _wstaw_firme(
        conn, samotny, detail_json={"id": samotny}, detail_utc=_teraz(clock), detail_state="pobrany"
    )
    # Wpis, który nigdy nie miał drugiej pisowni, nie jest duplikatem i nie ma być liczony.
    czysty = "A2BC372F-7412-4293-8729-4739614FF3D7"
    _wstaw_firme(
        conn, czysty, detail_json={"id": czysty}, detail_utc=_teraz(clock), detail_state="pobrany"
    )
    conn.commit()
    conn.close()

    with Store(sciezka, environment="test", clock=clock) as store:
        assert (store.merged_duplicates, store.renamed_identifiers) == (2, 1)


def test_baza_bez_duplikatow_nie_jest_ruszana(sciezka: Path, clock: FakeClock) -> None:
    conn = _baza_v2(sciezka, clock)
    _wstaw_firme(
        conn, UPPER, detail_json=SZCZEGOLY, detail_utc=_teraz(clock), detail_state="pobrany"
    )
    _przypnij(conn, "r1", UPPER)
    conn.commit()
    przed = _stan(conn)
    conn.close()

    with Store(sciezka, environment="test", clock=clock) as store:
        assert (store.merged_duplicates, store.renamed_identifiers) == (0, 0)
        assert _stan(store._conn) == przed


def test_powtorne_otwarcie_niczego_nie_zmienia(sciezka: Path, clock: FakeClock) -> None:
    """Idempotencja: migracja biegnie raz, a licznik przy drugim otwarciu milczy.

    Gdyby `merged_duplicates` był liczony przy każdym otwarciu, operator dostawałby przy
    każdym uruchomieniu meldunek o naprawie, która wydarzyła się raz."""
    conn = _baza_v2(sciezka, clock)
    _zaslepka(conn, LOWER)
    _wstaw_firme(
        conn, UPPER, detail_json=SZCZEGOLY, detail_utc=_teraz(clock), detail_state="pobrany"
    )
    _przypnij(conn, "r1", LOWER)
    conn.commit()
    conn.close()

    with Store(sciezka, environment="test", clock=clock) as store:
        assert (store.merged_duplicates, store.renamed_identifiers) == (1, 0)
        po_pierwszym = _stan(store._conn)

    with Store(sciezka, environment="test", clock=clock) as store:
        assert (store.merged_duplicates, store.renamed_identifiers) == (0, 0)
        assert _stan(store._conn) == po_pierwszym


def test_powtorzona_migracja_na_zmigrowanej_bazie_nic_nie_scala(
    sciezka: Path, clock: FakeClock
) -> None:
    """Idempotencja **treści**, nie strażnika wersji.

    „Drugie otwarcie nic nie zmienia" dowodzi tylko tyle, że `PRAGMA user_version` działa —
    migracja przy takim otwarciu w ogóle nie rusza. Własność, która się liczy, brzmi inaczej:
    powtórka po awarii w połowie jest bezpieczna. Awaria mogła przecież nastąpić po zapisach,
    a przed podbiciem znacznika, więc cofamy znacznik na 2 na już zmigrowanej bazie i pytamy
    o drugi **pełny** przebieg: ma nie znaleźć nic do scalenia i zostawić tabele bez zmian."""
    _baza_z_polowkami(sciezka, clock)
    with Store(sciezka, environment="test", clock=clock) as store:
        assert (store.merged_duplicates, store.renamed_identifiers) == (1, 0)
        po_migracji = _stan(store._conn)

    conn = sqlite3.connect(sciezka)
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()

    with Store(sciezka, environment="test", clock=clock) as store:
        assert (store.merged_duplicates, store.renamed_identifiers) == (0, 0)
        assert _stan(store._conn) == po_migracji
        rekord = next(iter(store.iter_run_records("r1")))
        assert rekord.list_json == LISTA and rekord.detail_json == SZCZEGOLY


# --------------------------------------------------------------------------- dwa procesy naraz


def _migruj_rownolegle(sciezka: Path, clock: FakeClock) -> list[tuple[int, int]]:
    """Dwa otwarcia tej samej bazy v2 startujące z jednej bariery; zwraca liczniki obu."""
    brama = threading.Barrier(2)
    zamek = threading.Lock()
    wyniki: list[tuple[int, int]] = []
    bledy: list[BaseException] = []

    def otworz() -> None:
        try:
            brama.wait(timeout=10)
            with Store(sciezka, environment="test", clock=clock) as store, zamek:
                wyniki.append((store.merged_duplicates, store.renamed_identifiers))
        except BaseException as exc:  # noqa: BLE001 — wyjątek z wątku ma dojechać do asercji
            with zamek:
                bledy.append(exc)

    watki = [threading.Thread(target=otworz, name=f"migracja-{i}") for i in range(2)]
    for watek in watki:
        watek.start()
    for watek in watki:
        watek.join(timeout=60)
    assert not any(w.is_alive() for w in watki), "migracja nie skończyła się w 60 s"
    assert bledy == [], f"otwarcie bazy rzuciło wyjątkiem: {bledy}"
    return wyniki


def test_dwa_procesy_migrujace_te_sama_baze_nie_gubia_scalonej_wartosci(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Wyścig dwóch migracji tej samej bazy — cisza, nie błąd, więc trzeba go pytać wprost.

    Zanim plan zaczął powstawać pod blokadą zapisu, dwa otwarcia w tym samym oknie budowały
    dwa plany z tego samego snapshotu. Drugi wykonywał wtedy `UPDATE ... = (SELECT ... FROM
    firma WHERE id = ?)` na wiersz źródłowy, którego pierwszy już usunął: podzapytanie
    zwracało NULL i kasowało dopiero co scaloną wartość. Oba procesy kończyły się sukcesem,
    `PRAGMA integrity_check` był czysty — objawem była wyłącznie pusta kolumna.

    Stąd dwie asercje i żadnej o przebiegu: obie połowy wpisu mają przetrwać, a meldunek
    o scaleniu ma paść **raz**, bo operator ma się dowiedzieć o jednej naprawie, nie o dwóch.
    Rundy są trzy, bo przeplot wątków jest niedeterministyczny: przy poprawnym kodzie każda
    kończy się tak samo, a to właśnie ta niezależność od przeplotu jest tu twierdzeniem."""
    for runda in range(3):
        sciezka = tmp_path / f"wyscig-{runda}.sqlite"
        _baza_z_polowkami(sciezka, clock)

        wyniki = _migruj_rownolegle(sciezka, clock)

        assert sorted(wyniki) == [(0, 0), (1, 0)], (
            f"runda {runda}: scalenie ma zameldować dokładnie jedno otwarcie, było {wyniki}"
        )
        with Store(sciezka, environment="test", clock=clock) as store:
            rekord = next(iter(store.iter_run_records("r1")))
            assert rekord.id == UPPER
            assert rekord.list_json == LISTA, f"runda {runda}: zgubiona połowa z listy"
            assert rekord.detail_json == SZCZEGOLY, f"runda {runda}: zgubione szczegóły"
            assert rekord.detail_state == "pobrany"
            nip = store._conn.execute("SELECT nip FROM firma WHERE id = ?", (UPPER,)).fetchone()[0]
            assert nip == "1234567890", f"runda {runda}: zgubiony skalar dawcy"


# --------------------------------------------------------------------------- skutek dla `wyczysc`


def test_scalony_wpis_przezywa_czyszczenie_ktore_zabralo_by_sierote(
    sciezka: Path, clock: FakeClock
) -> None:
    """Po co scalać, zamiast po prostu kasować zaślepki: sierota ginie przy `wyczysc`.

    `purge_older_than` kasuje wiersze `firma`, których nie trzyma żaden run. Przed migracją
    komplet szczegółów jest właśnie takim wierszem — dane kupione żądaniami znikają przy
    pierwszym czyszczeniu i nikt tego nie zauważa, bo run i tak pokazywał pustkę."""
    conn = _baza_v2(sciezka, clock)
    stary_utc = utc_iso(clock.wall() - 400 * 86_400)
    _zaslepka(conn, LOWER)
    _wstaw_firme(conn, UPPER, detail_json=SZCZEGOLY, detail_utc=stary_utc, detail_state="pobrany")
    _przypnij(conn, "r1", LOWER)
    conn.commit()
    sieroty = [
        str(r[0])
        for r in conn.execute(
            "SELECT id FROM firma WHERE id NOT IN (SELECT firma_id FROM run_firma)"
        )
    ]
    conn.close()
    assert sieroty == [UPPER], "przed migracją szczegóły nie są trzymane przez żaden run"

    with Store(sciezka, environment="test", clock=clock) as store:
        runy, rekordy = store.purge_older_than(30)
        assert (runy, rekordy) == (0, 0)
        rekord = next(iter(store.iter_run_records("r1")))
        assert rekord.detail_json == SZCZEGOLY
