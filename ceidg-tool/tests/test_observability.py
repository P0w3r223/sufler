"""Warstwa obserwowalności wyprowadzona z nocnego przebiegu 2026-09-07/08 (ADR-0013).

Ten przebieg nie wywrócił się — po prostu nic o sobie nie powiedział. Trzy rodzaje milczenia
dały się wtedy odróżnić dopiero po fakcie i po ludzku:

* maszyna spała 9 h 50 min w środku `aktualizuj`, a blokada bazy wygasa po 600 s, więc proces
  pisał do bazy bez ważnej dzierżawy i nikt go o tym nie uprzedził;
* w logu zostały dwie dziury, godzinna i dziesięciogodzinna, **nie do odróżnienia**, bo
  `on_wait` szedł wyłącznie na ekran — postój limitera i śpiąca maszyna wyglądają tak samo,
  gdy jedynym śladem jest brak śladu;
* podsumowanie brzmiało „Zmienionych wpisów: 13401, szczegółów: 0." — liczba poprawna, bez
  punktu odniesienia, więc „0" czytało się jak „nic nie trzeba było dopisywać".

Testy pytają więc o to, co **zostaje po sesji**: treść komunikatu, wpis w logu i zdanie
podsumowania. Asercje są o tekście, bo tekst jest tu produktem — mechanizm, który zadziałał
i nie zostawił śladu, jest w tym projekcie równoważny mechanizmowi, którego nie ma.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import ceidg_tool.pipeline as pipeline
from ceidg_tool.clock import local_hhmm
from ceidg_tool.config import Settings
from ceidg_tool.criteria import Criteria
from ceidg_tool.errors import ResumableError
from ceidg_tool.logsetup import LOGGER_NAME
from ceidg_tool.pipeline import (
    LOG_WAIT_S,
    Deps,
    LockHeartbeat,
    LockLostError,
    RunResult,
    _LogEvents,
    build_deps,
)
from ceidg_tool.recordid import kanoniczne_id
from ceidg_tool.store import DEFAULT_LOCK_STALE_S, Store
from ceidg_tool.ui import flow, texts
from tests.conftest import FakeClock
from tests.support import FakeApi


@dataclass
class NagrywaneZdarzenia:
    """Pełna implementacja protokołu `Events`, która tylko zapamiętuje.

    Każda metoda protokołu jest tu obecna i każda coś notuje — dzięki temu test dekoratora
    może pytać nie tylko o to, co dekorator dopisał, ale i o to, czego nie zgubił po drodze."""

    wywolania: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)
    komunikaty: list[str] = field(default_factory=list)
    zamkniete: int = 0

    def on_request(self, endpoint: str, status: int, elapsed_s: float) -> None:
        self.wywolania.append(("on_request", (endpoint, status, elapsed_s)))

    def on_wait(self, seconds: float, reason: str, resume_at_epoch: float) -> None:
        self.wywolania.append(("on_wait", (seconds, reason, resume_at_epoch)))

    def on_page(self, page_index: int, records: int, total: int | None) -> None:
        self.wywolania.append(("on_page", (page_index, records, total)))

    def on_details(self, done: int, total: int) -> None:
        self.wywolania.append(("on_details", (done, total)))

    def on_export(self, done: int, total: int) -> None:
        self.wywolania.append(("on_export", (done, total)))

    def on_download(self, done_bytes: int, total_bytes: int | None) -> None:
        self.wywolania.append(("on_download", (done_bytes, total_bytes)))

    def on_model(self, elapsed_s: float, tokens: int) -> None:
        self.wywolania.append(("on_model", (elapsed_s, tokens)))

    def on_message(self, text: str) -> None:
        self.wywolania.append(("on_message", (text,)))
        self.komunikaty.append(text)

    def close(self) -> None:
        self.wywolania.append(("close", ()))
        self.zamkniete += 1

    def nazwy(self) -> list[str]:
        return [nazwa for nazwa, _ in self.wywolania]


class LogSpy(logging.Handler):
    """Zbiera wpisy logu pakietu bez zaglądania do pliku.

    Własny handler, a nie `caplog`: `logsetup.setup_logging` ustawia loggerowi pakietu
    `propagate = False`, więc wpisy nie muszą dojść do korzenia, na którym `caplog` wiesza
    swój handler. Test o logu, który milczy zależnie od tego, czy inny test wcześniej
    skonfigurował logowanie, byłby gorszy niż brak testu."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.wpisy: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.wpisy.append(record)

    def teksty(self, poziom: int | None = None) -> list[str]:
        return [r.getMessage() for r in self.wpisy if poziom is None or r.levelno == poziom]


@pytest.fixture
def log_spy() -> Iterator[LogSpy]:
    logger = logging.getLogger(LOGGER_NAME)
    spy = LogSpy()
    poprzedni = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(spy)
    try:
        yield spy
    finally:
        logger.removeHandler(spy)
        logger.setLevel(poprzedni)


@pytest.fixture
def zdarzenia() -> NagrywaneZdarzenia:
    return NagrywaneZdarzenia()


@pytest.fixture
def deps(tmp_path: Path, clock: FakeClock, zdarzenia: NagrywaneZdarzenia) -> Iterator[Deps]:
    """Zależności bez klienta — bicie serca dotyczy bazy, nie sieci.

    Zdarzenia idą przez prawdziwy szew kompozycji z `build_deps`, więc komunikat
    z `LockHeartbeat` przechodzi po drodze przez `_LogEvents` i `_LockHeartbeatEvents`,
    dokładnie jak w produkcji."""
    settings = Settings(token="tok", environment="test", data_dir=tmp_path / "dane")
    zbudowane = build_deps(settings, clock=clock, events=zdarzenia, online=False)
    try:
        yield zbudowane
    finally:
        zbudowane.store.close()


def _serce(deps: Deps) -> LockHeartbeat:
    """Bicie serca **tego** kompletu zależności, a nie świeżo zbudowane na boku.

    `build_deps` tworzy jedną instancję i wstrzykuje ją zarówno pętlom `pipeline`, jak
    i limiterowi (jako `heartbeat` plastrów). Detektor snu odejmuje od siebie dwa kolejne
    bicia, więc ma sens tylko wtedy, gdy widzi wszystkie — test na własnej instancji
    sprawdzałby obiekt, którego produkcja nie używa."""
    assert deps.heartbeat is not None, "build_deps nie wstrzyknął bicia serca"
    return deps.heartbeat


def _cudza_blokada(deps: Deps, *, swiezosc_s: float) -> None:
    """Wiersz `run_lock` z cudzym PID-em i zadanym wiekiem bicia serca."""
    with deps.store._conn:
        deps.store._conn.execute(
            "INSERT INTO run_lock(environment, pid, started_utc, heartbeat_epoch) "
            "VALUES ('test', ?, '2026-09-08T02:00:00Z', ?) "
            "ON CONFLICT(environment) DO UPDATE SET pid = excluded.pid, "
            "heartbeat_epoch = excluded.heartbeat_epoch",
            (os.getpid() + 1, deps.clock.wall() - swiezosc_s),
        )


# --------------------------------------------------------------- bicie serca i utrata blokady


def test_bicie_serca_przy_wlasnej_blokadzie_milczy(
    deps: Deps, zdarzenia: NagrywaneZdarzenia
) -> None:
    """Norma nie ma być zdarzeniem — inaczej alarm utonie w szumie."""
    deps.store.acquire_lock(force=False)
    serce = LockHeartbeat(deps.store, deps.events, deps.clock)

    serce.bij()

    assert zdarzenia.komunikaty == []


def test_wygasla_blokada_bez_wlasciciela_jest_odzyskiwana_z_komunikatem(
    deps: Deps, zdarzenia: NagrywaneZdarzenia, log_spy: LogSpy
) -> None:
    """Blokada przepadła, ale nikt jej nie zajął — praca ma iść dalej, nie paść.

    Ten przypadek jest produkcyjny: proces zasnął, dzierżawa wygasła, nikt inny nie ruszył.
    Przerwanie runu byłoby tu karą za to, że komputer się uśpił."""
    deps.store.acquire_lock(force=False)
    serce = LockHeartbeat(deps.store, deps.events, deps.clock)
    _cudza_blokada(deps, swiezosc_s=DEFAULT_LOCK_STALE_S * 2)  # cudza, ale dawno wygasła

    serce.bij()

    assert any("odzyskana" in k for k in zdarzenia.komunikaty), zdarzenia.komunikaty
    assert any("przepadła" in t for t in log_spy.teksty(logging.WARNING))
    trzymajacy = deps.store._conn.execute(
        "SELECT pid FROM run_lock WHERE environment = 'test'"
    ).fetchone()[0]
    assert int(trzymajacy) == os.getpid(), "blokada nie wróciła do tego procesu"


def test_blokada_przejeta_przez_zywy_proces_przerywa_run(deps: Deps) -> None:
    """Druga strona tej samej decyzji: pisanie do bazy bez dzierżawy jest gorsze niż przerwa.

    `LockLostError` jest `ResumableError`, więc checkpoint stoi, run kończy się jako
    `przerwany`, a operator dostaje `wznow` — przerwanie ma tu cenę jednego polecenia,
    a nie utraconej pracy."""
    deps.store.acquire_lock(force=False)
    serce = LockHeartbeat(deps.store, deps.events, deps.clock)
    _cudza_blokada(deps, swiezosc_s=0.0)  # cudza i żywa

    with pytest.raises(LockLostError) as wyjatek:
        serce.bij()

    assert isinstance(wyjatek.value, ResumableError)
    assert "inny proces" in str(wyjatek.value).lower()


def test_sen_maszyny_jest_nazwany_po_imieniu_z_liczba_minut(
    deps: Deps, clock: FakeClock, zdarzenia: NagrywaneZdarzenia, log_spy: LogSpy
) -> None:
    """Dokładnie ten pomiar z produkcji: 9 h 50 min snu przy blokadzie wygasającej po 600 s.

    Skok mierzy się na zegarze **ściennym**, bo monotoniczny w czasie snu stoi — i to
    rozejście się obu zegarów jest jedynym sygnałem, jaki proces po przebudzeniu ma.
    Komunikat niesie liczbę minut, bo tłumaczy naraz trzy rzeczy, które operator widzi
    osobno: rozjechany czas zakończenia, pustą historię żądań i wygasłą blokadę."""
    deps.store.acquire_lock(force=False)
    serce = LockHeartbeat(deps.store, deps.events, deps.clock)
    clock.jump_wall(9 * 3600 + 50 * 60)

    serce.bij()

    komunikat = next(k for k in zdarzenia.komunikaty if "spał" in k)
    assert "590 min" in komunikat
    assert "Blokada bazy mogła w tym czasie wygasnąć" in komunikat
    assert any("spał" in t for t in log_spy.teksty(logging.WARNING))


def test_krotka_przerwa_nie_jest_nazywana_snem(
    deps: Deps, clock: FakeClock, zdarzenia: NagrywaneZdarzenia
) -> None:
    """Próg to wiek dzierżawy: krótsza przerwa nie mogła jej unieważnić, więc milczy.

    Bez tej asercji „wykrywanie snu" spełniałby też mechanizm alarmujący zawsze — a wtedy
    godzinny postój limitera czytałby się jak awaria sprzętu."""
    deps.store.acquire_lock(force=False)
    serce = LockHeartbeat(deps.store, deps.events, deps.clock)
    clock.jump_wall(DEFAULT_LOCK_STALE_S - 1)

    serce.bij()

    assert [k for k in zdarzenia.komunikaty if "spał" in k] == []


def test_kolejne_bicie_mierzy_przerwe_od_poprzedniego_a_nie_od_startu(
    deps: Deps, clock: FakeClock, zdarzenia: NagrywaneZdarzenia
) -> None:
    """Znacznik przesuwa się przy każdym biciu — inaczej po jednym śnie alarm zostałby na stałe."""
    deps.store.acquire_lock(force=False)
    serce = LockHeartbeat(deps.store, deps.events, deps.clock)
    clock.jump_wall(DEFAULT_LOCK_STALE_S * 2)
    serce.bij()
    assert len([k for k in zdarzenia.komunikaty if "spał" in k]) == 1

    serce.bij()

    assert len([k for k in zdarzenia.komunikaty if "spał" in k]) == 1


# --------------------------------------------------------------------- log przeżywający sesję


def _log_events(zdarzenia: NagrywaneZdarzenia, clock: FakeClock) -> _LogEvents:
    return _LogEvents(zdarzenia, clock)


def test_dlugie_czekanie_zostawia_w_logu_godzine_wznowienia(
    zdarzenia: NagrywaneZdarzenia, clock: FakeClock, log_spy: LogSpy
) -> None:
    """Sedno poprawki: sam czas trwania nie czyni dziury w logu policzalną.

    Linia „wznowienie planowane na 02:14" obok następnej linii ostemplowanej na 12:04 mówi,
    że proces stał dziesięć godzin **dłużej**, niż zamierzał. Bez godziny wznowienia obie
    dziury z tamtej nocy wyglądały tak samo."""
    wznowienie = clock.wall() + 3600.0
    _log_events(zdarzenia, clock).on_wait(3600.0, "limit API", wznowienie)

    wpis = next(t for t in log_spy.teksty() if "czekam" in t)
    assert local_hhmm(wznowienie) in wpis
    assert "limit API" in wpis
    assert zdarzenia.wywolania == [("on_wait", (3600.0, "limit API", wznowienie))]


@pytest.mark.parametrize(
    ("sekundy", "w_logu"),
    [(LOG_WAIT_S - 0.1, False), (LOG_WAIT_S, True), (LOG_WAIT_S + 60, True)],
)
def test_prog_logowania_czekania(
    zdarzenia: NagrywaneZdarzenia, clock: FakeClock, log_spy: LogSpy, sekundy: float, w_logu: bool
) -> None:
    """Odstęp limitera (3,75 s) pada tysiące razy — w logu zostawiłby sam szum.

    Próg jest po to, żeby log niósł postoje, a nie rytm. Zdarzenie idzie dalej niezależnie
    od progu: pasek na ekranie ma swój własny sposób na krótkie przerwy."""
    _log_events(zdarzenia, clock).on_wait(sekundy, "odstęp", clock.wall() + sekundy)

    assert any("czekam" in t for t in log_spy.teksty()) is w_logu
    assert zdarzenia.nazwy() == ["on_wait"], "zdarzenie ma iść dalej niezależnie od progu"


def test_komunikat_trafia_do_logu_bez_znakow_sterujacych(
    zdarzenia: NagrywaneZdarzenia, clock: FakeClock, log_spy: LogSpy
) -> None:
    """Plik logu czyta się przez terminal (`type`, `cat`, `tail`), więc jest drugim ekranem.

    Nazwy z rejestru bywają wrogie, a `richtext.safe` chroni tylko `rich`. Do wnętrza
    dekoratora idzie tekst oryginalny — neutralizacja dotyczy zapisu, nie przekazania dalej,
    bo warstwa wyżej ma własny sposób na ten sam problem."""
    wrogi = "Firma \x1b[31mCZERWONA\x07 \x00 sp. z o.o."
    _log_events(zdarzenia, clock).on_message(wrogi)

    zapisane = next(t for t in log_spy.teksty() if "Firma" in t)
    assert "\x1b" not in zapisane and "\x07" not in zapisane and "\x00" not in zapisane
    # ESC znika, widoczne „[31m" zostaje — `strip_control` usuwa znaki sterujące, a nie
    # tekst wokół nich; dwie spacje to ślad po dwóch usuniętych znakach sterujących.
    assert zapisane == "Firma [31mCZERWONA  sp. z o.o."
    assert zdarzenia.komunikaty == [wrogi]


def test_dekorator_przepuszcza_close(zdarzenia: NagrywaneZdarzenia, clock: FakeClock) -> None:
    """`close()` należy do protokołu, bo żywy pasek `rich` nadpisuje wszystko po sobie.

    Dekorator, który go zje, zostawia pasek przy życiu do końca sesji — a wtedy podsumowanie
    i pytanie o zapis są rysowane pod nim i program wygląda na zawieszony."""
    _log_events(zdarzenia, clock).close()

    assert zdarzenia.zamkniete == 1


def test_dekorator_nie_gubi_zadnego_zdarzenia(
    zdarzenia: NagrywaneZdarzenia, clock: FakeClock
) -> None:
    """Dekorator ma dokładać ślad w logu, a nie filtrować protokół.

    Gdyby zjadł `on_page` albo `on_details`, pasek postępu zamilkłby — czyli powstałby
    dokładnie ten objaw, którego cała ta warstwa ma pilnować."""
    log = _log_events(zdarzenia, clock)
    log.on_request("firmy", 200, 0.4)
    log.on_page(0, 25, 100)
    log.on_details(5, 100)
    log.on_export(1, 2)
    log.on_download(1024, 4096)
    log.on_model(1.5, 300)
    log.on_message("cokolwiek")
    log.on_wait(1.0, "odstęp", clock.wall())
    log.close()

    assert zdarzenia.nazwy() == [
        "on_request",
        "on_page",
        "on_details",
        "on_export",
        "on_download",
        "on_model",
        "on_message",
        "on_wait",
        "close",
    ]


# ------------------------------------------------------------------ zdanie dla operatora


def test_podsumowanie_bez_gubionych_wpisow_nie_alarmuje() -> None:
    """Zero niewyjaśnionych to norma i ma brzmieć jak norma."""
    zdanie = texts.update_summary(13401, 13401, 0)

    assert zdanie == "Zmienionych wpisów: 13401, szczegółów: 13401."


def test_podsumowanie_z_nocy_2026_09_08_niesie_ostrzezenie() -> None:
    """Zdanie, które tamtej nocy nie miało się do czego odnieść.

    „Zmienionych wpisów: 13401, szczegółów: 0." było poprawne co do liczb i całkowicie
    mylące co do sensu: „0" czytało się jak „nic nie trzeba było dopisywać", a znaczyło
    „wszystko przepadło". Punktem odniesienia jest liczba wpisów bez wyjaśnienia."""
    zdanie = texts.update_summary(13401, 0, 13401)

    assert "Zmienionych wpisów: 13401, szczegółów: 0." in zdanie
    assert "13401 wpisów zostało bez szczegółów i bez wyjaśnienia" in zdanie
    assert "logu" in zdanie


def test_domyslny_brak_alarmu_nie_zmienia_starego_zdania() -> None:
    """Trzeci argument jest domyślny, więc stare wywołania mają brzmieć tak samo jak dotąd."""
    assert texts.update_summary(5, 5) == texts.update_summary(5, 5, 0)


# ------------------------------------------------- bicie serca bez blokady, której nie wzięto


def test_bicie_serca_bez_wzietej_blokady_niczego_nie_bierze(
    deps: Deps, zdarzenia: NagrywaneZdarzenia
) -> None:
    """Eksport, `runy` i sonda blokady nie biorą — bicie serca nie ma czego bronić.

    `touch_lock()` zwraca `False` w dwóch różnych sytuacjach: gdy dzierżawę przejął ktoś inny
    i gdy wiersza `run_lock` nie ma wcale. Bez `holds_lock` obie znaczyły to samo, więc bicie
    serca w operacji czysto lokalnej wchodziło w gałąź odzyskiwania i przez `acquire_lock`
    po cichu **brało** blokadę, której ta operacja nie potrzebuje — blokując pobieranie
    w drugim oknie terminala."""
    serce = LockHeartbeat(deps.store, deps.events, deps.clock)
    assert deps.store.holds_lock is False

    serce.bij()

    assert zdarzenia.komunikaty == []
    wiersze = deps.store._conn.execute("SELECT COUNT(*) FROM run_lock").fetchone()[0]
    assert int(wiersze) == 0, "bicie serca wzięło blokadę, której operacja nie potrzebuje"


def test_po_zwolnieniu_blokady_bicie_serca_znowu_milczy(
    deps: Deps, zdarzenia: NagrywaneZdarzenia
) -> None:
    """`holds_lock` gaśnie w `release_lock` — po oddaniu blokady nie ma czego pilnować.

    Bez zerowania flagi każde bicie po zakończeniu runu (na przykład w eksporcie idącym
    zaraz po pobraniu) próbowałoby ją odzyskać i zakładało wiersz od nowa."""
    deps.store.acquire_lock(force=False)
    serce = LockHeartbeat(deps.store, deps.events, deps.clock)
    deps.store.release_lock()
    assert deps.store.holds_lock is False

    serce.bij()

    assert zdarzenia.komunikaty == []
    wiersze = deps.store._conn.execute("SELECT COUNT(*) FROM run_lock").fetchone()[0]
    assert int(wiersze) == 0


def test_z_wzieta_blokada_utrata_dzierzawy_dalej_przerywa(deps: Deps) -> None:
    """Kontrola drugiej strony `holds_lock`: gdy blokadę wzięto, `False` znaczy utratę.

    Ta asercja pilnuje, żeby poprawka nie wyciszyła alarmu przy okazji — najtańszy sposób
    na „operacja bez blokady milczy" to przecież milczeć zawsze."""
    deps.store.acquire_lock(force=False)
    assert deps.store.holds_lock is True
    serce = LockHeartbeat(deps.store, deps.events, deps.clock)
    _cudza_blokada(deps, swiezosc_s=0.0)

    with pytest.raises(LockLostError):
        serce.bij()


# ------------------------------------------------------------------- tryb dziennika bazy


def test_baza_plikowa_pracuje_w_wal(deps: Deps) -> None:
    """`PRAGMA journal_mode` **zwraca** tryb wynikowy; nieprzeczytany był tylko życzeniem."""
    assert deps.store.journal_mode == "wal"


def test_baza_w_pamieci_ma_wlasny_tryb(clock: FakeClock) -> None:
    """Baza w pamięci nie ma dziennika i nie ma o czym ostrzegać."""
    with Store(":memory:", environment="test", clock=clock) as store:
        assert store.journal_mode == "memory"


class StoreBezWal(Store):
    """Magazyn zgłaszający tryb inny niż WAL — tak wygląda baza na dysku sieciowym.

    Podmiana przy szwie `pipeline.Store`, bo tryb dziennika zależy od systemu plików,
    a nie od kodu: wymuszenie go na prawdziwej bazie wymagałoby zasobu, którego test nie ma."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.journal_mode = "delete"


def test_ostrzezenie_gdy_baza_nie_pracuje_w_wal(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tryb inny niż WAL zmienia odporność na przerwanie — operator ma o tym wiedzieć.

    Bez WAL czytelnik i pisarz blokują się nawzajem, a przerwany proces zostawia dziennik
    do odtworzenia. Najczęstsza przyczyna to plik bazy na dysku sieciowym i taka jest
    podpowiedź w komunikacie."""
    monkeypatch.setattr(pipeline, "Store", StoreBezWal)
    settings = Settings(token="tok", environment="test", data_dir=tmp_path / "dane")

    zbudowane = build_deps(settings, clock=clock, online=False)

    ostrzezenie = next(w for w in zbudowane.warnings if "dziennika" in w)
    assert "'delete'" in ostrzezenie and "WAL" in ostrzezenie
    assert "dysku sieciowym" in ostrzezenie
    zbudowane.store.close()


def test_wal_nie_generuje_ostrzezenia(deps: Deps) -> None:
    assert [w for w in deps.warnings if "dziennika" in w] == []


# ------------------------------------------------- licznik wpisów bez szczegółów i bez wyjaśnienia


def test_licznik_niewyjasnionych_liczy_wpisy_w_stanie_brak(deps: Deps) -> None:
    """Pozytywna strona alarmu: zaślepka bez rozstrzygnięcia **jest** liczona.

    Bez tej asercji „licznik milczy" spełniałby też licznik zwracający zawsze zero — a wtedy
    nocny przebieg z 13 401 zgubionymi wpisami znów przeszedłby bez słowa. Zaślepki zakłada
    `link_ids`, dokładnie jak w trybie `/zmiana`, więc kształt jest produkcyjny."""
    store = deps.store
    run_id = store.start_run(
        run_id="r-alarm",
        criteria_json="{}",
        criteria_hash="h",
        profile_hash="p",
        mode="szczegoly",
        tool_version="0",
        cursor_mode="links",
        kind="zmiana",
    )
    ids = kanoniczne_id([f"18578BAF-BAC7-42B9-AA7E-4C3666132E{i:02X}" for i in range(3)])
    store.link_ids(run_id, page_index=0, ids=ids)

    assert store.count_run_unresolved(run_id) == 3

    store.save_details(details=[{"id": ids[0], "nazwa": "Firma"}], missing_ids=[ids[1]])

    # `pobrany` i `nieznaleziony` to rozstrzygnięcia — zostaje jeden bez odpowiedzi.
    assert store.count_run_unresolved(run_id) == 1


def test_wlasne_czekanie_limitera_nie_melduje_sie_jako_sen_maszyny(
    tmp_path: Path, clock: FakeClock, zdarzenia: NagrywaneZdarzenia
) -> None:
    """Detektor snu musi widzieć bicia z plastrów limitera, inaczej myli postój z awarią.

    Godzinny postój budżetowy jest **naszą** decyzją i program o nim wie z góry. Gdyby
    limiter dostawał do plastrów `store.touch_lock` zamiast tego samego obiektu, dzierżawa
    byłaby odświeżana, ale znacznik ostatniego bicia stałby w miejscu — i pierwsze bicie po
    postoju meldowałoby „komputer prawdopodobnie spał" po każdym dłuższym czekaniu.
    Ostrzeżenie, które pada zawsze, przestaje cokolwiek znaczyć; a sen **w środku** czekania
    ma się nadal wykrywać, bo tam przerwa między dwoma plastrami naprawdę rośnie."""
    api = FakeApi()
    api.add_fixture("firmy_limit1.json")
    settings = Settings(token="tok", environment="test", data_dir=tmp_path / "dane")
    deps = build_deps(settings, clock=clock, events=zdarzenia, http=api.client())
    assert deps.client is not None
    deps.client.count(Criteria())  # pierwsze żądanie wchodzi bez czekania
    deps.client._limiter.note_budget(0, clock.wall() + 3600.0)

    deps.client.count(Criteria())  # godzinny postój budżetowy, przespany w plastrach
    _serce(deps).bij()  # bicie pętli tuż po postoju

    assert [k for k in zdarzenia.komunikaty if "spał" in k] == [], (
        "zaplanowany postój limitera zgłoszony jako sen maszyny"
    )
    deps.store.close()


def _wynik_runu(*, unresolved: int) -> RunResult:
    return RunResult(
        run_id="r1",
        status="zakonczony",
        records=100,
        details=100 - unresolved,
        pages=1,
        count_api=100,
        requests=21,
        unresolved=unresolved,
    )


def test_pobierz_ze_szczegolami_dopina_uwage_o_gubionych_wpisach() -> None:
    """Alarm należy do obu ścieżek, nie tylko do `aktualizuj`.

    `unresolved` był liczony od początku, ale czytało go wyłącznie podsumowanie `aktualizuj`.
    Operator, któremu `pobierz --szczegoly` zgubiłby część wpisów, nie zobaczyłby nic — czyli
    dokładnie ten sam kształt („policzone i nieprzeczytane"), który ta faza zamyka, tylko na
    świeższym egzemplarzu."""
    wynik = flow._from_run(_wynik_runu(unresolved=3))

    assert wynik.notes == (texts.unresolved_note(3),)
    assert "3 wpisów" in wynik.notes[0]


def test_bez_gubionych_wpisow_pobranie_nie_dokleja_uwagi() -> None:
    """Zero to norma — uwaga „0 wpisów zostało bez szczegółów" byłaby szumem przy każdym runie."""
    assert flow._from_run(_wynik_runu(unresolved=0)).notes == ()


def test_obie_sciezki_niosa_dokladnie_to_samo_zdanie() -> None:
    """Jedno źródło zdania dla `aktualizuj` i dla `pobierz --szczegoly`.

    Dwie kopie rozjechałyby się przy pierwszej poprawce, a to jest zdanie o cichej stracie:
    operator ma je rozpoznać niezależnie od tego, którym poleceniem trafił na problem."""
    zdanie = texts.unresolved_note(7)

    assert texts.update_summary(20, 13, 7).endswith(zdanie)
    assert flow._from_run(_wynik_runu(unresolved=7)).notes == (zdanie,)
