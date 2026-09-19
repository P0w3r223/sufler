"""Długie operacje: czy operator widzi, że program pracuje, i czy blokada z nim nadąża.

Ten plik jest przedłużeniem `tests/test_update_progress.py` na dwie pozostałe ścieżki,
które w produkcji trwają minutami: pobranie raportu (ZIP 21 MB, CSV 68 MB, 287 tys. wierszy
— `docs/decisions.md`) i eksport skoroszytu. Obie działały funkcjonalnie poprawnie i obie
milczały, a milczenie kilkuminutowe jest defektem samo w sobie: operator zabija proces,
który pracuje. §A uzupelnienie-01.md wymaga paska postępu „w trakcie pobierania" bez wyjątków
dla źródła danych.

Pomiar sprzed poprawek (2026-09-06, sonda offline na tej maszynie) — to on wyznaczył skalę:

- `run_report_fetch`, 20 000 wierszy CSV, kryteria pasujące do jednego z nich:
  **jedno** zdarzenie postępu i **zero** bić serca blokady w całym przebiegu;
- `run_export`, 20 000 rekordów: 34,6 s i **zero** zdarzeń, czyli 1,7 ms na rekord;
  w skali raportu wojewódzkiego (287 tys.) około ośmiu minut ciszy między „Raport pobrany"
  a tabelą podsumowania.

Sześć defektów opisanych tutaj zostało zamkniętych 2026-09-06; komentarz nad testem mówi,
który to i co dokładnie się zmieniło. Testy zostają jako zabezpieczenie przed nawrotem,
bo żaden z nich nie wynika ze specyfikacji — wszystkie z pomiaru i z przejścia po kodzie.

Każdy test o defekcie ma parę: test kontrolny na ścieżce, która robi to samo poprawnie.
Ta para jest tu równie ważna jak sam test. Bez niej czerwony wynik był nie do odróżnienia
od zepsutego stelaża, a zielony — od testu, który niczego nie dotyka.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from openpyxl import load_workbook
from rich.console import Console

from ceidg_tool.config import Settings, safe_filename
from ceidg_tool.console import ConsoleEvents
from ceidg_tool.criteria import Criteria
from ceidg_tool.errors import ExportError
from ceidg_tool.exporter import EXPORT_REPORT_EVERY, write_csv, write_workbook
from ceidg_tool.normalizer import NormalizedRecord, normalize
from ceidg_tool.pipeline import (
    REPORT_PAGE_SIZE,
    Deps,
    build_deps,
    run_export,
    run_fetch,
    run_report_fetch,
)
from ceidg_tool.records import RawRecord, Report, RowContext
from ceidg_tool.store import DEFAULT_LOCK_STALE_S
from tests.conftest import FakeClock, list_record
from tests.support import FakeApi, criteria
from tests.test_pipeline_e2e import paged_api, settings_for
from tests.test_reports import HEADER, row

BASE = "https://test-dane.biznes.gov.pl/api/ceidg/v3"


def firmy_ids_of(path: Path) -> list[Any]:
    """Identyfikatory z arkusza `Firmy` — czy podział na części czegoś nie zgubił."""
    ws = load_workbook(path)["Firmy"]
    column = [c.value for c in ws[1]].index("id")
    return [wiersz[column] for wiersz in ws.iter_rows(min_row=2, values_only=True)]


REPORT = Report(
    "r1",
    "Zarejestrowane działalności - województwo podlaskie",
    ".csv",
    f"{BASE}/raport/r1",
    "2026-09-04 06:40:59",
)

# Tyle wierszy skanuje ten test. Prawdziwy raport wojewódzki ma 287 tys.; tutaj chodzi
# wyłącznie o to, żeby wierszy było wielokrotnie więcej niż `REPORT_PAGE_SIZE`.
SCANNED_ROWS = 4 * REPORT_PAGE_SIZE

# Eksport tylu rekordów trwa tu około pół sekundy; 287 tys. to około ośmiu minut.
EXPORTED_RECORDS = 200


class Recorder:
    """Odbiorca `Events`, który tylko zapisuje, co i w jakiej kolejności zostało zgłoszone."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def on_request(self, endpoint: str, status: int, elapsed_s: float) -> None:
        self.events.append(("zadanie", endpoint))

    def on_wait(self, seconds: float, reason: str, resume_at_epoch: float) -> None:
        self.events.append(("czekanie", reason))

    def on_page(self, page_index: int, records: int, total: int | None) -> None:
        self.events.append(("strona", (page_index, records, total)))

    def on_details(self, done: int, total: int) -> None:
        self.events.append(("szczegoly", (done, total)))

    def on_export(self, done: int, total: int) -> None:
        self.events.append(("eksport", (done, total)))

    def on_download(self, done_bytes: int, total_bytes: int | None) -> None:
        self.events.append(("pobieranie", (done_bytes, total_bytes or 0)))

    def on_model(self, elapsed_s: float, tokens: int) -> None:
        self.events.append(("model", (elapsed_s, tokens)))

    def on_message(self, text: str) -> None:
        self.events.append(("komunikat", text))

    def close(self) -> None:
        self.events.append(("koniec_paska", None))

    def progress_signals(self) -> list[tuple[str, Any]]:
        """Zdarzenia, które w konsoli poruszają paskiem albo wypisują zdanie.

        Lista wymienia kanały, nie jeden wybrany — `on_export` dołączył tu razem
        z własnym paskiem eksportu i test o eksporcie ma go uznać za znak życia
        tak samo jak `on_page`.

        `on_request` do niej nie należy: `ConsoleEvents.on_request` tylko zwiększa licznik,
        więc żądanie samo z siebie nie jest dla operatora znakiem życia. `close` też nie —
        gaszenie paska jest końcem pracy, nie jej objawem.
        """
        return [
            e
            for e in self.events
            if e[0] in ("strona", "szczegoly", "eksport", "pobieranie", "komunikat")
        ]

    def scan_signals(self) -> list[tuple[str, Any]]:
        """Sam kanał skanowania. Testy o rytmie pętli raportu liczą tylko to, co ta pętla
        zgłasza — inaczej zdarzenia pobierania archiwum dawałyby im drugi powód do zieleni."""
        return [e for e in self.events if e[0] == "strona"]


def report_zip(tmp_path: Path, rows: list[dict[str, str]], name: str = "raport.zip") -> Path:
    """Archiwum w kształcie prawdziwego raportu. Kolumny brane po nazwie z `HEADER`,
    nie z kolejności `dict` — inaczej literówka w `row(...)` przesuwałaby cały wiersz."""
    lines = [";".join(HEADER)]
    lines.extend(";".join(r[column] for column in HEADER) for r in rows)
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Zarejestrowane działalności.csv", "﻿" + "\n".join(lines) + "\n")
    return path


def rows_with_one_match(count: int) -> list[dict[str, str]]:
    """`count` wierszy, z których dokładnie ostatni leży w mieście, o które pyta test."""
    return [
        row(Nip=f"{1_000_000_000 + i}", Miejscowosc="Białystok" if i == count - 1 else "Łomża")
        for i in range(count)
    ]


def report_deps(tmp_path: Path, clock: FakeClock, archive: Path, events: Recorder) -> Deps:
    api = FakeApi()
    api.fallback = lambda request: httpx.Response(200, content=archive.read_bytes())
    return build_deps(
        Settings(token="tok", environment="test", data_dir=tmp_path / "dane"),
        clock=clock,
        http=api.client(),
        events=events,
    )


def heartbeat_spy(deps: Deps) -> list[int]:
    """Podgląd bić serca blokady; zwraca listę indeksów zdarzeń, przy których padły."""
    beats: list[int] = []
    real = deps.store.touch_lock

    def spy() -> bool:
        beats.append(1)
        # Wynik musi wrócić: od 2026-09-08 `touch_lock` melduje `False`, gdy blokadę
        # przejął inny proces, a `LockHeartbeat` na tym `False` przerywa run. Atrapa
        # gubiąca wartość zwracaną zamieniała każdy podgląd bicia serca w utratę blokady
        # — ten sam kształt co `ScriptedPrompter.confirm` z `bool(answer)`.
        return real()

    deps.store.touch_lock = spy  # type: ignore[method-assign]
    return beats


# --------------------------------------------------------- raport: postęp podczas skanowania


# Defekt zamknięty 2026-09-06: pętla raportu odmierza postęp co `REPORT_PAGE_SIZE`
# **przeskanowanych** wierszy. Wcześniej liczyła rekordy **dopasowane**, więc wąskie
# kryteria dawały jedno zdarzenie na cały przebieg przez 287 tys. wierszy.
def test_the_report_scan_reports_progress_even_when_almost_nothing_matches(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Cisza mierzona w przeczytanych wierszach, nie w zapisanych rekordach.

    Praca, którą wykonuje ta pętla, to przeczytanie i zmapowanie **każdego** wiersza CSV —
    filtr odrzuca go dopiero po `row_to_record`. Kryteria „podlaskie + Białystok" na raporcie
    wojewódzkim odrzucają zdecydowaną większość wierszy, więc dopóki próg odmierzał rekordy
    dopasowane, pasek nie drgnął ani razu, choć program czytał 68 MB.

    Próg wzięty z produkcji, nie z sufitu: `REPORT_PAGE_SIZE` to granulacja, którą kod już
    sobie wybrał dla zapisu do bazy. Test pyta, czy tą samą granulacją mierzona jest praca,
    a nie jej wynik.
    """
    events = Recorder()
    archive = report_zip(tmp_path, rows_with_one_match(SCANNED_ROWS))
    deps = report_deps(tmp_path, clock, archive, events)

    result = run_report_fetch(criteria(wojewodztwo="podlaskie", miasto="Białystok"), deps, REPORT)
    deps.store.close()

    assert result.records == 1  # tyle wierszy pasuje — i tyle pracy widać na pasku
    assert len(events.scan_signals()) >= SCANNED_ROWS // REPORT_PAGE_SIZE, (
        f"{SCANNED_ROWS} przeskanowanych wierszy dało {len(events.scan_signals())} znaków życia"
    )


def test_the_report_scan_does_report_progress_when_everything_matches(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Kontrola do testu wyżej: przy szerokich kryteriach pasek działa.

    Bez tej pary poprzedni test byłby nie do odróżnienia od „ścieżka raportu w ogóle nie
    zna zdarzeń" — a ona je zna, tylko liczy nimi niewłaściwą rzecz.
    """
    events = Recorder()
    archive = report_zip(tmp_path, rows_with_one_match(SCANNED_ROWS))
    deps = report_deps(tmp_path, clock, archive, events)

    result = run_report_fetch(criteria(wojewodztwo="podlaskie"), deps, REPORT)
    deps.store.close()

    assert result.records == SCANNED_ROWS
    assert len(events.scan_signals()) >= SCANNED_ROWS // REPORT_PAGE_SIZE


# --------------------------------------------------------- raport: bicie serca blokady


# Defekt zamknięty 2026-09-06: `run_report_fetch` bije teraz w blokadę po każdej zapisanej
# stronie rekordów. Wcześniej nie robił tego ani razu — pobranie 21 MB i przemielenie 68 MB CSV
# szło pod blokadą wygasającą po `DEFAULT_LOCK_STALE_S`.
def test_the_lock_gets_a_heartbeat_while_the_report_is_being_processed(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Blokada wygasa po dziesięciu minutach, a ta ścieżka nie biła ani razu.

    Ten sam defekt, który znaleziono w `aktualizuj` (patrz `test_update_progress.py`), tyle
    że tu żył dłużej — i jest groźniejszy, bo `run_report_fetch` trzyma blokadę przez cały
    strumieniowy zapis do bazy. Pobranie ZIP-a na wolnym łączu plus skan 287 tys. wierszy
    potrafi przekroczyć próg; wtedy drugi proces bierze blokadę na tym samym tokenie,
    a pierwszy nadal pisze do tej samej bazy.

    Uwaga: sam `save_page` **nie** odświeża heartbeatu — sprawdzone w `store.save_page`,
    więc zapisywanie stron nie zastępuje bicia serca; potrzebny jest jawny `touch_lock`.
    """
    events = Recorder()
    archive = report_zip(tmp_path, rows_with_one_match(SCANNED_ROWS))
    deps = report_deps(tmp_path, clock, archive, events)
    beats = heartbeat_spy(deps)

    run_report_fetch(criteria(wojewodztwo="podlaskie"), deps, REPORT)
    deps.store.close()

    assert beats, (
        f"ani jednego bicia serca; blokada wygasa po {DEFAULT_LOCK_STALE_S / 60:.0f} min "
        "pracy pod nią"
    )


# Defekt zamknięty 2026-09-06 razem z poprzednim: pierwsza poprawka powiesiła bicie serca
# na `save_page`, czyli znów na rekordach dopasowanych — ta para testów pokazała, że wąskie
# kryteria wracają wtedy do stanu sprzed poprawki.
def test_the_lock_gets_a_heartbeat_even_when_almost_nothing_matches(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Druga połowa tego samego defektu, której poprawka z 2026-09-06 nie objęła.

    Bicie serca dopisano najpierw obok `store.save_page`, więc odmierzała je ta sama
    wielkość, co pasek postępu: rekordy **dopasowane**. Praca pod blokadą to jednak
    przeczytanie całego CSV, a nie zapisanie tego, co przeszło filtr. Zapytanie
    „podlaskie + jedno miasto" mieli komplet 287 tys. wierszy i nie zapisuje ani jednej
    pełnej strony — blokada wracała wtedy do stanu sprzed poprawki.

    Test wyżej przechodził także z tamtą połowiczną poprawką, bo pyta o całe województwo.
    Ta para stoi tu po to, żeby poprawki nie dało się uznać za domkniętą po jednym
    scenariuszu — i to ona wymusiła przebudowę pętli zamiast dopisania jednej linijki.
    """
    events = Recorder()
    archive = report_zip(tmp_path, rows_with_one_match(SCANNED_ROWS))
    deps = report_deps(tmp_path, clock, archive, events)
    beats = heartbeat_spy(deps)

    result = run_report_fetch(criteria(wojewodztwo="podlaskie", miasto="Białystok"), deps, REPORT)
    deps.store.close()

    assert result.records == 1
    assert beats, (
        f"{SCANNED_ROWS} przeskanowanych wierszy bez ani jednego bicia serca; "
        f"blokada wygasa po {DEFAULT_LOCK_STALE_S / 60:.0f} min"
    )


def test_the_api_fetch_beats_the_lock_on_every_page(tmp_path: Path, clock: FakeClock) -> None:
    """Kontrola: zwykłe pobranie przez API bije po każdej stronie.

    To jest ta sama para, co wyżej — pokazuje, że stelaż testu widzi bicia serca i że
    ścieżka raportu jest wyjątkiem, a nie regułą całego pakietu.
    """
    api = paged_api()
    deps = build_deps(settings_for(tmp_path), clock=clock, http=api.client())
    deps.profile = deps.profile.model_copy(update={"max_limit_firmy": 5})
    assert deps.client is not None
    deps.client._profile = deps.profile
    beats = heartbeat_spy(deps)

    result = run_fetch(criteria(wojewodztwo="podlaskie"), deps, known_count=15)
    deps.store.close()

    assert result.pages == 3
    assert len(beats) >= result.pages


# --------------------------------------------------------- raport: przerwanie przez operatora


def interrupt_the_report_scan(monkeypatch: pytest.MonkeyPatch, *, after_rows: int) -> None:
    """Ctrl+C w połowie skanowania CSV — pętla raportu trwa minutami, więc to realny moment."""
    import ceidg_tool.pipeline as pipeline
    from ceidg_tool.reports import iter_report_rows as original

    def interrupted(path: Path) -> Iterator[dict[str, str]]:
        for index, parsed in enumerate(original(path)):
            if index >= after_rows:
                raise KeyboardInterrupt("przerwane przez użytkownika")
            yield parsed

    monkeypatch.setattr(pipeline, "iter_report_rows", interrupted)


# Defekt zamknięty 2026-09-06: `run_report_fetch` łapie `KeyboardInterrupt`
# i `ResumableError` i oznacza run jako `przerwany`. Wcześniej miał tylko `except CeidgError`.
def test_ctrl_c_during_a_report_marks_the_run_as_interrupted(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run, który mówi „w toku", a nie jest w toku, kłamie w każdym późniejszym ekranie.

    `run_fetch` i `run_update` przechwytywały `KeyboardInterrupt` i oznaczały run jako
    `przerwany`. `run_report_fetch` miał tylko `except CeidgError`, więc po Ctrl+C zostawał
    zapis, którego nikt już nie ruszy: wznowić się nie da (`kind != 'firmy'`), a menu
    niedokończonych pobrań pyta o `kind='firmy'`, więc go nie pokaże. Asymetria między
    trzema bliźniaczymi funkcjami była tu argumentem: to nie decyzja, tylko przeoczenie.
    """
    events = Recorder()
    archive = report_zip(tmp_path, rows_with_one_match(50))
    deps = report_deps(tmp_path, clock, archive, events)
    interrupt_the_report_scan(monkeypatch, after_rows=10)

    with pytest.raises(KeyboardInterrupt):
        run_report_fetch(criteria(wojewodztwo="podlaskie"), deps, REPORT)
    runs = deps.store.list_runs()
    deps.store.close()

    assert len(runs) == 1
    assert runs[0].status == "przerwany", f"run raportu został jako {runs[0].status!r}"


# Defekt zamknięty 2026-09-06: `purge_older_than` nie omija już bezterminowo runów
# `w_toku` — kryterium jest czas ostatniego zapisu starszy niż próg wygaśnięcia blokady.
def test_a_report_abandoned_by_ctrl_c_still_falls_under_retention(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Konsekwencja statusu, która wychodzi poza wygodę: dane osobowe zostają na zawsze.

    `purge_older_than` z rozmysłem omijał `w_toku` — inaczej retencja skasowałaby run
    pracującego właśnie procesu. Dopóki jednak istniała ścieżka, która zostawia `w_toku`
    po procesie martwym, ten wyjątek był dziurą w retencji: rekordy przypięte przez
    `run_firma` nie są osierocone, więc drugie zapytanie `purge_older_than` też ich nie
    ruszy. Pełny zrzut województwa to komplet nazwisk i adresów — i zostawał na zawsze.
    """
    events = Recorder()
    archive = report_zip(tmp_path, rows_with_one_match(2 * REPORT_PAGE_SIZE))
    deps = report_deps(tmp_path, clock, archive, events)
    # przerwanie po pierwszej zapisanej stronie: w bazie leży komplet danych osobowych
    interrupt_the_report_scan(monkeypatch, after_rows=REPORT_PAGE_SIZE + 50)

    with pytest.raises(KeyboardInterrupt):
        run_report_fetch(criteria(wojewodztwo="podlaskie"), deps, REPORT)
    assert deps.store.count_records_for_runs([deps.store.list_runs()[0].run_id]) == REPORT_PAGE_SIZE

    clock.advance(365 * 86_400)
    removed_runs, removed_records = deps.store.purge_older_than(30)
    remaining = deps.store.list_runs()
    deps.store.close()

    assert (removed_runs, remaining) == (1, [])
    assert removed_records == REPORT_PAGE_SIZE, "rekordy raportu zostały w bazie po retencji"


def test_ctrl_c_during_an_api_fetch_marks_the_run_as_interrupted(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kontrola: ścieżka API robi to poprawnie — i to jest wzorzec dla raportu."""
    api = paged_api()
    deps = build_deps(settings_for(tmp_path), clock=clock, http=api.client())
    deps.profile = deps.profile.model_copy(update={"max_limit_firmy": 5})
    assert deps.client is not None
    deps.client._profile = deps.profile

    import ceidg_tool.pipeline as pipeline

    original_fetch_list = pipeline._fetch_list

    def interrupted(query: Criteria, dependencies: Deps, run_id: str) -> None:
        original_fetch_list(query, dependencies, run_id)
        raise KeyboardInterrupt("przerwane przez użytkownika")

    monkeypatch.setattr(pipeline, "_fetch_list", interrupted)

    with pytest.raises(KeyboardInterrupt):
        run_fetch(criteria(wojewodztwo="podlaskie"), deps, known_count=15)
    runs = deps.store.list_runs()
    deps.store.close()

    assert [r.status for r in runs] == ["przerwany"]


# --------------------------------------------------------- eksport: znak życia przy zapisie


def report_run_with(tmp_path: Path, clock: FakeClock, records: int) -> tuple[Deps, str, Recorder]:
    """Zakończony run z `records` rekordami — najtańszy sposób na materiał do eksportu."""
    events = Recorder()
    archive = report_zip(tmp_path, rows_with_one_match(records))
    deps = report_deps(tmp_path, clock, archive, events)
    result = run_report_fetch(criteria(wojewodztwo="podlaskie"), deps, REPORT)
    assert result.records == records
    return deps, result.run_id, events


# Defekt zamknięty 2026-09-06: `write_workbook` przyjmuje `Events` i raportuje `on_export`
# w obu przejściach. Wcześniej zapis skoroszytu był ciszą od pierwszego do ostatniego wiersza —
# zmierzone 453 firmy/s, czyli ponad dziesięć minut dla pełnego województwa z raportu.
def test_an_export_tells_the_operator_that_it_is_working(tmp_path: Path, clock: FakeClock) -> None:
    """Ostatni odcinek sesji był najdłuższym niemym odcinkiem w całym programie.

    `run_export` przechodzi po źródle rekordów dwa razy (`plan_export`, potem `_write_part`)
    i dopiero na końcu oddaje sterowanie, żeby `ui` wypisało tabelę podsumowania. Pomiar
    na tej maszynie: 20 000 rekordów to 34,6 s, czyli 1,7 ms na rekord — pełny raport
    wojewódzki (287 tys.) to około ośmiu minut, w których nie padało ani jedno zdanie.
    Z punktu widzenia operatora program zawieszał się dokładnie po komunikacie o pobraniu.

    Ten test pyta o *jakikolwiek* znak życia i dziś spełnia go samo zdanie „Zapisuję
    skoroszyt…". To za mało na osiem minut — o ruch paska pyta osobno test niżej.
    """
    deps, run_id, events = report_run_with(tmp_path, clock, EXPORTED_RECORDS)
    events.events.clear()

    summary = run_export(run_id, tmp_path / "wynik.xlsx", deps)
    deps.store.close()

    assert summary.records == EXPORTED_RECORDS
    assert events.progress_signals(), (
        f"eksport {EXPORTED_RECORDS} rekordów bez ani jednego znaku życia"
    )


# Defekt zamknięty 2026-09-06: `run_export` podaje `deps.events` do `write_workbook`.
# Wcześniej cały kanał `on_export` — zbudowany, otypowany, z własnym paskiem w `ConsoleEvents`
# — nie dostawał ani jednego zdarzenia, bo brakowało tego jednego argumentu.
def test_the_export_bar_actually_moves_while_the_workbook_is_written(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Zdanie na starcie to nie pasek postępu; §A wymaga paska, a nie zapowiedzi.

    `exporter` umie już raportować: `plan_export` i `_write_part` wołają `on_export` co
    `EXPORT_REPORT_EVERY` firm, a `ConsoleEvents.on_export` ma dla tego własne zadanie
    „Zapis skoroszytu". Brakuje ostatniego przeskoku — `run_export` nie podaje `deps.events`
    do `write_workbook`, więc cała ta instalacja jest martwa i nikt tego nie widzi: test
    pytający ogólnie „czy coś powiedział" zalicza się na jednym `on_message` sprzed zapisu.

    Liczba rekordów jest większa niż `EXPORT_REPORT_EVERY` z rozmysłem — chodzi o raport
    **w trakcie** przechodzenia po rekordach, nie o jeden na końcu.
    """
    deps, run_id, events = report_run_with(tmp_path, clock, EXPORT_REPORT_EVERY + 50)
    events.events.clear()

    run_export(run_id, tmp_path / "wynik.xlsx", deps)
    deps.store.close()

    moves = [e for e in events.events if e[0] == "eksport"]
    assert len(moves) >= 2, f"pasek zapisu dostał {len(moves)} aktualizacji"


def test_the_fetch_that_produced_that_run_did_tell_the_operator(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Kontrola do testu wyżej: ten sam `Recorder` na tym samym `Deps` zdarzenia dostaje.

    Gdyby jej zabrakło, czerwony wynik powyżej mógłby wynikać z niepodpiętego odbiorcy
    zdarzeń, a nie z braku raportowania w eksporcie.
    """
    deps, _run_id, events = report_run_with(tmp_path, clock, EXPORTED_RECORDS)
    deps.store.close()

    assert events.progress_signals()


# --------------------------------------------------------- co pasek naprawdę pokazuje


def rendered_bar(pages: list[tuple[int, int, int | None]]) -> str:
    """Prawdziwy `ConsoleEvents` na buforze — to, co zobaczyłby operator w terminalu."""
    buffer = io.StringIO()
    events = ConsoleEvents(Console(file=buffer, force_terminal=True, width=100, color_system=None))
    for index, records, total in pages:
        events.on_page(index, records, total)
        events._progress_bar().refresh()
    events.close()
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", buffer.getvalue())


# Defekt zamknięty 2026-09-06: własna kolumna `_CountColumn` pokazuje `ukończone/?` przy
# nieznanej sumie. Wcześniej `TextColumn('{task.completed}/{task.total}')` drukowało „None".
def test_the_progress_bar_never_shows_the_word_none_as_a_total() -> None:
    """Pasek raportu przez cały przebieg pokazuje `2000/None` i nie ma ETA.

    Tak woła `run_report_fetch`: `on_page(page_index, len(buffer), None)` po każdej zapisanej
    stronie i dopiero na końcu `on_page(page_index, len(buffer), matched)`. Sumy nie da się
    tu poznać wcześniej — zna ją dopiero koniec skanu — więc pasek musi umieć wyglądać
    sensownie bez niej. Dosłowne „None" na ekranie operator czyta jak awarię.

    Test patrzy na wyrenderowany tekst, a nie na argumenty wołania: to jedyny poziom,
    na którym widać, co naprawdę zobaczy człowiek.
    """
    output = rendered_bar([(0, 1000, None), (1, 1000, None), (2, 300, 2300)])

    assert "None" not in output, "pasek pokazuje dosłowne 'None' zamiast sumy albo pustego pola"


def test_the_progress_bar_shows_real_numbers_when_the_total_is_known() -> None:
    """Kontrola: ścieżka API zna `count` od pierwszej strony i pasek wygląda tak, jak ma."""
    output = rendered_bar([(0, 25, 75), (1, 25, 75), (2, 25, 75)])

    assert "75/75" in output
    assert "None" not in output


# --- odporność i koszt eksportu ----------------------------------------------------------


def test_a_corrupt_cached_report_is_replaced_instead_of_failing_forever(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Uszkodzony ZIP w pamięci podręcznej musi mieć wyjście, a nie pętlę tego samego błędu.

    `if not dest.exists()` widziało obecny plik jako gotowy, więc przerwane pobieranie albo
    pełny dysk zostawiały archiwum, przez które **każda** kolejna próba kończyła się tak
    samo — komunikat podawał ścieżkę, ale nikt nie mówił, że plik trzeba usunąć."""
    events = Recorder()
    zdrowy = report_zip(tmp_path, rows_with_one_match(10))
    deps = report_deps(tmp_path, clock, zdrowy, events)

    cache = deps.settings.data_dir / "raporty"
    cache.mkdir(parents=True, exist_ok=True)
    uszkodzony = cache / safe_filename(f"{REPORT.nazwa}_{REPORT.utworzono[:10]}", ".zip")
    uszkodzony.write_bytes(b"to nie jest ZIP")

    result = run_report_fetch(criteria(wojewodztwo="podlaskie"), deps, REPORT)
    deps.store.close()

    assert result.status == "zakonczony"
    assert result.records == 10  # kryterium wojewódzkie obejmuje wszystkie wiersze próbki
    komunikaty = [str(v) for kind, v in events.events if kind == "komunikat"]
    assert any("uszkodzony" in m.lower() for m in komunikaty), komunikaty


def test_a_split_export_reads_the_source_once_per_pass_not_once_per_part(tmp_path: Path) -> None:
    """Każda część czytała źródło od początku i porzucała rekordy części wcześniejszych.

    `islice(source(), offset, offset + size)` przewija, a przewijanie tutaj znaczy: zapytanie
    do SQLite plus `normalize` dla każdego pominiętego rekordu. Koszt rósł kwadratowo
    z liczbą części, a próg podziału jest osiągalny przy raporcie wojewódzkim."""
    wywolania = {"n": 0}
    rekordy = [
        normalize(
            RawRecord(
                id=f"{i:08d}-0000-0000-0000-000000000000",
                list_json=list_record(1),
                detail_json=None,
                list_utc="2026-09-05T09:00:00Z",
                detail_utc=None,
                detail_state="brak",
                zrodlo="CEIDG_API",
            ),
            RowContext(srodowisko="test", pobrano_utc="2026-09-05T10:00:00Z"),
        )
        for i in range(12)
    ]

    def source() -> Iterator[NormalizedRecord]:
        wywolania["n"] += 1
        return iter(rekordy)

    paths = write_workbook(
        tmp_path / "duzy.xlsx", source, metadata=[("srodowisko", "test")], row_limit=4
    )

    assert len(paths) > 2, "test ma sens dopiero przy kilku częściach"
    # jedno przejście planujące i jeden strumień na wszystkie części
    assert wywolania["n"] == 2, f"źródło otwarto {wywolania['n']} razy przy {len(paths)} częściach"
    zapisane = [id_ for p in paths for id_ in firmy_ids_of(p)]
    assert len(zapisane) == len(set(zapisane)) == len(rekordy)  # nic nie zgubione, nic dwa razy


def test_csv_export_reads_the_source_once_for_all_sheets(tmp_path: Path) -> None:
    """Jedno przejście na wszystkie arkusze, nie jedno na każdy.

    `write_csv` wołało `source()` w pętli po `DATA_SHEETS`, więc `--formaty csv` czytało bazę
    i normalizowało każdy rekord cztery razy; razem z `xlsx` i `jsonl` dawało to około siedmiu
    pełnych przebiegów po SQLite dla jednego eksportu."""
    wywolania = {"n": 0}
    rekordy = [
        normalize(
            RawRecord(
                id=f"{i:08d}-0000-0000-0000-000000000000",
                list_json=list_record(1),
                detail_json=None,
                list_utc="2026-09-05T09:00:00Z",
                detail_utc=None,
                detail_state="brak",
                zrodlo="CEIDG_API",
            ),
            RowContext(srodowisko="test", pobrano_utc="2026-09-05T10:00:00Z"),
        )
        for i in range(5)
    ]

    def source() -> Iterator[NormalizedRecord]:
        wywolania["n"] += 1
        return iter(rekordy)

    paths = write_csv(tmp_path / "csv", source)

    assert wywolania["n"] == 1, f"źródło otwarto {wywolania['n']} razy"
    # rekordy bez szczegółów nie mają wierszy PKD, więc powstaje sam obowiązkowy arkusz
    assert [p.name for p in paths] == ["firmy.csv"]
    firmy = (tmp_path / "csv" / "firmy.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(firmy) == 1 + len(rekordy)  # nagłówek plus wszystkie rekordy, nic nie zgubione


def test_a_failed_csv_export_leaves_no_half_written_files(tmp_path: Path) -> None:
    """Pliki powstają obok celu i są przemianowywane dopiero po udanym zapisie **całości**.

    Zapis czterech arkuszy naraz nie jest transakcją, ale żaden plik nie może pojawić się
    ucięty w połowie — a to jest różnica, która liczy się dla kogoś, kto potem czyta CSV."""

    def source() -> Iterator[NormalizedRecord]:
        yield normalize(
            RawRecord(
                id="00000000-0000-0000-0000-000000000000",
                list_json=list_record(1),
                detail_json=None,
                list_utc="2026-09-05T09:00:00Z",
                detail_utc=None,
                detail_state="brak",
                zrodlo="CEIDG_API",
            ),
            RowContext(srodowisko="test", pobrano_utc="2026-09-05T10:00:00Z"),
        )
        raise OSError("dysk pełny")

    dest = tmp_path / "csv"
    with pytest.raises(ExportError, match="Nie można zapisać plików CSV"):
        write_csv(dest, source)

    assert not list(dest.glob("*.csv"))
    assert not list(dest.glob(".*.tmp"))  # nic nie zostaje po nieudanej próbie


# --------------------------------------------------- raport: cisza podczas pobierania archiwum


# Defekt zamknięty 2026-09-07 (ADR-0010, znalezisko F3): między `_acquire_lock` a pierwszym
# `touch_lock()` — tysiąc wierszy CSV dalej — leżał cały transfer archiwum. Prawdziwy raport
# wojewódzki to 21 MB, a przy zerwanym łączu dochodzi drabinka ponowień (10+30+60+300 s),
# każde od zera. `DEFAULT_LOCK_STALE_S` wynosi 600 s, więc blokada potrafiła wygasnąć pod
# procesem, który pracował — i to jest ta sama rodzina defektów co strony `/zmiana` w fazie 3e,
# tylko mierzona w bajtach zamiast w żądaniach.


def test_the_report_download_beats_the_lock_and_moves_the_bar(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Transfer archiwum sam z siebie bije w blokadę i melduje postęp.

    Test patrzy na bicia serca, które padły **przed** pierwszym wierszem CSV: dopiero one
    dowodzą, że przerwa między wzięciem blokady a skanem została zasypana. Liczenie wszystkich
    bić razem przechodziłoby dzięki tym, które pochodzą ze skanu — czyli z kodu, którego ten
    test nie dotyczy.
    """
    events = Recorder()
    archive = report_zip(tmp_path, rows_with_one_match(REPORT_PAGE_SIZE))
    deps = report_deps(tmp_path, clock, archive, events)
    # Bicie serca notuje, ile zdarzeń padło przed nim — dzięki temu wiadomo nie tylko *ile*
    # ich było, ale i *kiedy*, a pytanie brzmi właśnie „czy przed skanem CSV".
    beats: list[int] = []
    real_touch = deps.store.touch_lock

    def touch_spy() -> bool:
        beats.append(len(events.events))
        # Wynik wraca — patrz komentarz przy `heartbeat_spy`.
        return real_touch()

    deps.store.touch_lock = touch_spy  # type: ignore[method-assign]

    run_report_fetch(criteria(wojewodztwo="podlaskie"), deps, REPORT)
    deps.store.close()

    kinds = [e[0] for e in events.events]
    first_scan_signal = kinds.index("strona") if "strona" in kinds else len(kinds)
    downloads = [e for e in events.events if e[0] == "pobieranie"]

    assert downloads, "pobranie archiwum nie zgłosiło ani jednego bajtu"
    assert downloads[0][1][0] == 0, "pierwsze zgłoszenie musi paść zaraz po nagłówkach"
    assert [b for b in beats if b < first_scan_signal], (
        "blokada nie dostała ani jednego bicia serca przed pierwszym wierszem CSV"
    )
    assert len(beats) >= len(downloads), "każde zgłoszenie transferu bije też w blokadę"


def test_a_cached_report_downloads_nothing_and_reports_nothing(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Kontrola: archiwum z pamięci podręcznej nie udaje transferu.

    Bez tej pary poprzedni test przechodziłby również przy implementacji, która melduje
    postęp pobierania zawsze — także wtedy, gdy nic nie jest pobierane.
    """
    events = Recorder()
    archive = report_zip(tmp_path, rows_with_one_match(REPORT_PAGE_SIZE))
    deps = report_deps(tmp_path, clock, archive, events)
    run_report_fetch(criteria(wojewodztwo="podlaskie"), deps, REPORT)
    deps.store.close()

    again = Recorder()
    deps2 = report_deps(tmp_path, clock, archive, again)
    run_report_fetch(criteria(wojewodztwo="podlaskie"), deps2, REPORT)
    deps2.store.close()

    assert [e for e in events.events if e[0] == "pobieranie"]
    assert not [e for e in again.events if e[0] == "pobieranie"]


def test_a_failing_transport_still_beats_the_lock_while_it_waits(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Drabinka ponowień nie dochodzi do nagłówków, więc postęp transferu nie ma czego zgłosić.

    Znalezisko z drugiego przeglądu 2026-09-07: `download_progress` pada dopiero, gdy płyną
    bajty. Cztery nieudane próby połączenia to 10+30+60+300 s plus limity czasu — do ~640 s
    przy blokadzie wygasającej po 600. Zasypuje to `_LockHeartbeatEvents.on_wait`, bo czekanie
    zawsze jest **zapowiadane** przed uśpieniem; ten test pilnuje, że bicia padają w oknie,
    w którym nie pobrano ani jednego bajtu.
    """
    events = Recorder()
    archive = report_zip(tmp_path, rows_with_one_match(REPORT_PAGE_SIZE))
    body = archive.read_bytes()
    attempts = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise httpx.ConnectError("sieć zniknęła", request=request)
        return httpx.Response(200, content=body)

    api = FakeApi()
    api.fallback = flaky
    deps = build_deps(
        Settings(token="tok", environment="test", data_dir=tmp_path / "dane"),
        clock=clock,
        http=api.client(),
        events=events,
    )
    beats: list[int] = []
    real_touch = deps.store.touch_lock

    def touch_spy() -> bool:
        beats.append(len(events.events))
        # Wynik wraca — patrz komentarz przy `heartbeat_spy`.
        return real_touch()

    deps.store.touch_lock = touch_spy  # type: ignore[method-assign]

    run_report_fetch(criteria(wojewodztwo="podlaskie"), deps, REPORT)
    deps.store.close()

    kinds = [e[0] for e in events.events]
    first_byte = kinds.index("pobieranie")
    waits_before_first_byte = [i for i, kind in enumerate(kinds) if kind == "czekanie"]

    assert attempts["n"] == 3, "test ma przejść przez dwie nieudane próby"
    assert [w for w in waits_before_first_byte if w < first_byte], "brak czekania przed bajtami"
    assert [b for b in beats if b < first_byte], (
        "blokada nie dostała bicia serca w czasie, gdy nie płynął ani jeden bajt"
    )


# ------------------------------------------- audyt 2026-09-07: pasek po nieudanym eksporcie


def test_a_failed_export_still_puts_out_the_progress_bar(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eksport, który padł, musi zgasić pasek — inaczej kreator wraca do menu z żywym `Live`.

    `run_fetch`, `run_report_fetch` i `run_update` gaszą pasek w `finally`; `run_export` robiło
    to wyłącznie na ścieżce szczęśliwej. Tymczasem eksport potrafi paść na brak miejsca, błąd
    zapisu i rekord z nadmiarem wierszy, a `wizard.run_wizard` łapie `CeidgError` i `OSError`
    i **wraca do menu**. Wraca wtedy z żywym paskiem, czyli w stan, w którym pytanie menu może
    w ogóle nie dotrzeć na ekran — dokładnie defekt, który wywrócił bramkę 3 w 2026-09-06,
    tylko wchodzący przez nieudany eksport (audyt 2026-09-07).
    """
    deps, run_id, events = report_run_with(tmp_path, clock, 10)
    events.events.clear()

    def pada(*args: object, **kwargs: object) -> None:
        raise OSError("dysk pełny")

    monkeypatch.setattr("ceidg_tool.pipeline.write_workbook", pada)

    with pytest.raises(OSError):
        run_export(run_id, tmp_path / "wynik.xlsx", deps)

    assert ("koniec_paska", None) in events.events, "pasek został żywy po nieudanym eksporcie"
    deps.store.close()
