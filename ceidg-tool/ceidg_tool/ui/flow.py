"""Kroki decyzyjne wspólne dla flag CLI, pliku YAML, trybu `--tak` i kreatora.

Jedno miejsce, w którym zapada kolejność: wznowienie → raport → rocznik PKD → zapytanie
o `count` → tabela kosztów → wybór → ewentualny podział na partie → pobranie → eksport →
podsumowanie (ADR-0008, rozszerzone w ADR-0012). Entry pointy różnią się wyłącznie tym, jaki
`Prompter` podstawią.

Liczba zapytań o `count`: **najwyżej dwa i tylko przed zgodą** — po jednym na populację, gdy
okres przejściowy PKD daje wybór. Bez takiego wyboru jedno, jak dotąd; po decyzji ani jednego.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

from ..batching import BatchPlan, plan_batches
from ..criteria import Criteria
from ..errors import ConfigError
from ..estimating import LARGE_COUNT_THRESHOLD, Estimate, estimate
from ..logsetup import LOG_FILE_NAME
from ..pipeline import (
    BatchOutcome,
    BatchResult,
    Deps,
    ExportSummary,
    RunInfo,
    RunResult,
    UpdatePlan,
    choose_report,
    count_hits,
    default_export_path,
    find_resumable,
    output_name,
    plan_update,
    run_batched_fetch,
    run_export,
    run_fetch,
    run_report_fetch,
)
from ..pkdmap import Rozszerzenie
from ..records import Report
from ..reports import report_covers
from . import prompts, texts
from .prompts import Prompter
from .texts import Block, SummaryInput

# `anuluj` to **nie** to samo co `wyjdz`. `wyjdz` znaczy „obejrzałem koszt i rezygnuję",
# więc kryteria są już rozstrzygnięte i warto zaproponować ich zapis. `anuluj` znaczy
# „nie chcę podejmować tej decyzji" — pada na pytaniu o rocznik PKD, gdzie nic jeszcze nie
# zapadło. Zlanie obu w jedno sprawiało, że kreator proponował zapis pliku z wyborem, którego
# operator właśnie odmówił dokonać (audyt 2026-09-07).
Decision = Literal["lista", "szczegoly", "raport", "wznow", "partie", "popraw", "wyjdz", "anuluj"]


class View(Protocol):
    """Wyjście: bloki i komunikaty. Konsola w programie, atrapa w testach."""

    def block(self, block: Block) -> None: ...

    def message(self, text: str) -> None: ...

    def warning(self, text: str) -> None: ...

    def error(self, text: str) -> None: ...


@dataclass(frozen=True)
class FetchPlan:
    """Wszystko, co ustalono przed pobraniem — bez żadnego dodatkowego żądania.

    Próg wędruje w planie, a nie osobnym argumentem `execute`: gdyby wykonanie brało go
    skądinąd, mogłoby dzielić partie inaczej, niż zapowiedziała tabela pokazana operatorowi."""

    criteria: Criteria
    count: int
    threshold: int
    estimate: Estimate | None = None
    report: Report | None = None
    resumable: RunInfo | None = None
    batches: BatchPlan | None = None


def collect_from_description(
    deps: Deps,
    prompter: Prompter,
    view: View,
    *,
    opis: str,
    today: date | None = None,
) -> Criteria | None:
    """Zdanie po polsku → potwierdzone `Criteria`. `None` znaczy „pytaj po kolei".

    Jedna implementacja dla kreatora i dla `pobierz --opis`, żeby oba wejścia nie mogły się
    rozjechać — ten sam argument, który w ADR-0008 wyprodukował całą warstwę `ui/`.

    Krok stoi **przed** `prepare_fetch`, więc niezmiennik „dokładnie jedno żądanie `count`",
    tabela kosztów i ścieżka zgody zostają nietknięte. Operator potwierdza interpretację,
    zanim wyda się choć jedno żądanie do CEIDG.
    """
    if deps.assistant is None:
        # Powód, gdy go znamy, zamiast domysłu. `load_pkd` potrafi powiedzieć „brak słownika
        # i oto polecenie, które go zbuduje" — zastępowanie tego zdaniem o kluczu było
        # wskazywaniem palcem na rzecz, która akurat działała (przebieg B6).
        raise ConfigError(deps.assistant_reason or texts.ASSISTANT_UNAVAILABLE)
    dzisiaj = today or datetime.now(tz=UTC).date()
    while True:
        # Zapowiedź **przed** paskiem, bo po jego uruchomieniu nic się już nie wypisze.
        # Bez niej operator patrzy na licznik bez skali i nie wie, czy to sekundy, czy minuty.
        view.message(texts.ASSISTANT_THINKING)
        try:
            wynik = deps.assistant.interpret(opis, dzisiaj=dzisiaj)
        finally:
            # Pasek gaśnie razem z pytaniem do modelu, a nie dopiero na końcu polecenia.
            # Póki żył, żywy `rich` nadpisywał interpretację, pytanie o zatwierdzenie i błąd,
            # gdyby padł — dokładnie ten defekt, który bramka 3 wyłapała 2026-09-06 na ścieżce
            # pobierania. Asystent to szósty kanał postępu i doszedł już po tamtej poprawce.
            deps.events.close()
        view.block(
            texts.interpretation(
                opis,
                wynik.kryteria.describe(),
                wynik.kody_pkd,
                [str(kod) for kod in wynik.ograniczenia],
            )
        )
        wybor = prompter.ask(prompts.ZATWIERDZ_INTERPRETACJE)
        if wybor == "tak":
            return wynik.kryteria
        if wybor == "popraw":
            opis = prompter.text(prompts.OPIS).strip()
            if not opis:
                return None
            continue
        if wybor == "pytania":
            return None
        raise prompts.CancelledError(texts.ASSISTANT_CANCELLED)


def show_first_screen(view: View, deps: Deps, *, version: str) -> None:
    view.block(
        texts.first_screen(deps.settings, now=datetime.now(tz=UTC), version=version, demo=deps.demo)
    )
    for warning in (*deps.settings.warnings, *deps.warnings):
        view.warning(warning)


def _z_rocznikiem(criteria: Criteria, kody_2007: tuple[str, ...]) -> Criteria:
    """Kopia kryteriów z ustawionym `pkd_2007`, **przepuszczona przez walidację**.

    `model_copy(update=…)` w pydanticu v2 waliduje pominąć — a to `_dedupe` sortuje krotki
    właśnie po to, żeby odcisk palca nie zależał od kolejności wejścia. Bez walidacji kandydat
    szeroki miał kody posortowane wewnątrz każdej połówki, ale nie razem, więc ta sama treść
    wczytana z pliku zapytania dawała **inny odcisk** i przerwany przebieg stawał się
    niewidoczny dla wznowienia (audyt 2026-09-07).
    """
    return Criteria.model_validate({**criteria.model_dump(), "pkd_2007": kody_2007})


def _kandydaci(
    criteria: Criteria, deps: Deps, wybor: bool | None
) -> tuple[Criteria, Criteria, Rozszerzenie | None]:
    """Buduje parę kandydatów (wąski, szeroki) dla okresu przejściowego PKD. Zero żądań.

    Wąski niesie już rozszerzenia **czyste** — kody 2007 znaczące dokładnie to samo co wybrane,
    więc ich dołożenie nie jest wyborem, tylko naprawą. Szeroki dokłada niejednoznaczne, czyli
    te, które wciągają cudzą branżę; różnica między kandydatami jest dokładnie tym, o co pyta
    `prompts.ROCZNIK_PKD`.

    Zwraca `Rozszerzenie` tylko wtedy, gdy jest o czym mówić — `None` znaczy „nic się nie
    zmienia", czyli najczęstszy przypadek (464 z 728 podklas).
    """
    if criteria.pkd_2007 and wybor is False:
        # Plik zapytania niesie rozszerzenie, a flaga mówi „bez rocznika 2007". Do przeglądu
        # 2026-09-07 wygrywał plik, po cichu: program pobierał populację szerszą, niż operator
        # przed chwilą zażądał, i nie mówił o tym ani słowa. Cisza o poszerzonym zapytaniu to
        # dokładnie ten defekt, dla którego powstał ADR-0012, więc sprzeczność jest błędem,
        # a nie sytuacją do rozstrzygnięcia domyślnie na czyjąś korzyść.
        raise ConfigError(texts.vintage_conflict(criteria.pkd_2007))
    if deps.pkd_map is None or not criteria.pkd or criteria.pkd_2007:
        # Pole już ustawione znaczy plik zapytania albo wcześniejszy wybór: powtórzenie
        # przebiegu ma dać ten sam wynik, więc niczego tu nie przeliczamy.
        return criteria, criteria, None
    rozsz = deps.pkd_map.rozszerz(criteria.pkd)
    if rozsz.is_empty():
        return criteria, criteria, None
    if wybor is False:
        # Jawne „bez rocznika 2007" albo przebieg nieinteraktywny. Kryteriów nie ruszamy, ale
        # `Rozszerzenie` **oddajemy**, bo bez niego nie dałoby się powiedzieć operatorowi,
        # czego zapytanie nie obejmuje — a milczenie jest tu całym defektem.
        return criteria, criteria, rozsz
    czyste = tuple(p.kod for p in rozsz.czyste)
    niejedno = tuple(p.kod for p in rozsz.niejednoznaczne)
    waskie = _z_rocznikiem(criteria, czyste) if czyste else criteria
    szerokie = _z_rocznikiem(criteria, (*czyste, *niejedno))
    if wybor is True:
        # `--pkd-2007` wybiera szeroko bez pytania; kandydaci schodzą się do jednego.
        return szerokie, szerokie, rozsz
    return waskie, szerokie, rozsz


def _zapytaj_o_rocznik(
    rozsz: Rozszerzenie,
    waskie: Criteria,
    szerokie: Criteria,
    prompter: Prompter,
    view: View,
    *,
    licznik_waski: int | None = None,
    licznik_szeroki: int | None = None,
) -> Criteria | None:
    """Pokazuje, co dołoży szerszy wybór, i pyta. `None` znaczy „wróć do menu"."""
    view.block(
        texts.vintage_offer(
            [(p.kod, p.nazwa, p.rowniez, p.dzis) for p in rozsz.niejednoznaczne],
            waskie=licznik_waski,
            szerokie=licznik_szeroki,
        )
    )
    odpowiedz = prompter.ask(prompts.ROCZNIK_PKD)
    if odpowiedz == "wyjdz":
        return None
    return szerokie if odpowiedz == "szerokie" else waskie


def prepare_fetch(
    criteria: Criteria,
    deps: Deps,
    prompter: Prompter,
    view: View,
    *,
    source: str = "auto",
    threshold: int = LARGE_COUNT_THRESHOLD,
    today: date | None = None,
    rocznik_2007: bool | None = None,
) -> tuple[Decision, FetchPlan]:
    """Ustala, co zrobić z kryteriami.

    Zapytania o `count`: **najwyżej dwa i tylko przed zgodą** — po jednym na populację, gdy
    okres przejściowy PKD daje wybór (ADR-0012, sub-decyzja 4). Bez takiego wyboru jest jedno,
    jak dotąd. Po decyzji operatora nie pada już żadne; pobranie korzysta z policzonego.
    Osobno dochodzi jedno zapytanie o listę raportów, gdy raport pokrywa kryteria.

    `rocznik_2007`: `None` znaczy „zapytaj, jeśli jest o co", `True` i `False` to jawny wybór
    z flagi albo z pliku zapytania — wtedy nie pytamy i nie liczymy drugi raz.
    """
    if criteria.is_empty():
        raise ConfigError(texts.EMPTY_CRITERIA)

    wejsciowe = criteria
    waskie, szerokie, rozsz = _kandydaci(criteria, deps, rocznik_2007)
    criteria = waskie
    if rozsz is not None and rocznik_2007 is False:
        view.warning(texts.vintage_skipped(rozsz.kody_2007))
    elif rozsz is not None:
        # Zastosowane bez pytania, ale **pokazane**: ciche poszerzenie zapytania to ten sam
        # defekt co cicha podmiana kodu przez model — wynik, którego operator nie wytłumaczy.
        # Przy `--pkd-2007` pokazujemy **wszystkie** dołożone kody, także niejednoznaczne:
        # flaga jest zgodą na dołożenie kodów, nie na nieoglądanie tego, co dokładają.
        pokazane = rozsz.czyste if rocznik_2007 is None else (*rozsz.czyste, *rozsz.niejednoznaczne)
        if pokazane:
            view.block(
                texts.vintage_applied([(p.kod, p.nazwa, p.rowniez, p.dzis) for p in pokazane])
            )
    pytac_o_rocznik = rozsz is not None and rozsz.wymaga_pytania and rocznik_2007 is None

    # Wznowienie sprawdzamy dla **wszystkich trzech** postaci kryteriów, bo każda ma własny
    # odcisk palca, a przerwany przebieg zapisał się pod tą, która obowiązywała wtedy:
    #   * `waskie`  — dzisiejsze wejście, z rozszerzeniem czystym, jeśli jakieś jest,
    #   * `szerokie` — przebieg, w którym ktoś wybrał „szerzej",
    #   * `wejsciowe` — bez żadnego rozszerzenia: tak zapisuje się `--tak`, `--bez-pkd-2007`
    #     i każdy przebieg sprzed ADR-0012.
    # Trzeciej postaci brakowało do przeglądu 2026-09-07 i nie wymagało to różnicy wersji:
    # harmonogram z `--tak` przerwany w połowie był niewidoczny dla ręcznego wejścia z tym samym
    # `--pkd`, więc operator zaczynał od zera, mając robotę w bazie. Baza, nie sieć — zero żądań.
    resumable = None
    do_wznowienia = criteria
    widziane_odciski: set[str] = set()
    for kandydat in (waskie, szerokie, wejsciowe):
        odcisk = kandydat.fingerprint()
        if odcisk in widziane_odciski:
            continue  # ta sama postać kryteriów; drugie zapytanie do bazy niczego nie doda
        widziane_odciski.add(odcisk)
        resumable = find_resumable(kandydat, deps)
        if resumable is not None:
            # Kandydat wędruje do **osobnej** zmiennej. Przypisanie go do `criteria` zapadało
            # przed pytaniem, więc odmowa zostawiała kryteria przy znalezionej populacji —
            # a reszta przepływu leciała nią, choć ekran zapowiedział co innego. Przy jawnej
            # fladze `--pkd-2007` odmowa wręcz kasowała jej działanie (audyt 2026-09-07).
            do_wznowienia = kandydat
            break
    if resumable is not None:
        view.message(
            texts.resume_offer(
                resumable.run_id,
                resumable.status,
                resumable.records_seen,
                do_wznowienia.describe(),
            )
        )
        if prompter.confirm(prompts.WZNOWIC, default=True):
            return "wznow", FetchPlan(
                criteria=do_wznowienia, count=0, threshold=threshold, resumable=resumable
            )

    report: Report | None = None
    if source in ("auto", "raport") and report_covers(criteria):
        report = choose_report(criteria, deps)
        if report is not None:
            view.block(texts.report_offer(report))
            if source == "raport" or prompter.confirm(prompts.UZYC_RAPORTU, default=True):
                if pytac_o_rocznik and rozsz is not None:
                    # Bez liczb, bo tu ich jeszcze nie ma — raport filtrujemy lokalnie, więc
                    # policzenie obu populacji wymagałoby najpierw ściągnięcia archiwum.
                    # Wybór zostaje przy operatorze; nieznana liczba jest lepsza od zmyślonej.
                    wybrane = _zapytaj_o_rocznik(rozsz, waskie, szerokie, prompter, view)
                    if wybrane is None:
                        return "anuluj", FetchPlan(criteria=criteria, count=0, threshold=threshold)
                    criteria = wybrane
                return "raport", FetchPlan(
                    criteria=criteria, count=0, threshold=threshold, report=report
                )
    if source == "raport" and report is None:
        raise ConfigError(
            "Żaden gotowy raport nie pokrywa tych kryteriów. Użyj --zrodlo api albo auto."
        )

    if pytac_o_rocznik and rozsz is not None:
        # Dwa `count` — po jednym na populację — i oba **przed** zgodą. To jest cała cena
        # przeformułowania niezmiennika: operator dostaje rozmiar tego, co ominie albo czego
        # nabierze, zamiast zdania „wynik może być niepełny", na które nie da się odpowiedzieć.
        licznik_waski = count_hits(waskie, deps)
        licznik_szeroki = count_hits(szerokie, deps)
        wybrane = _zapytaj_o_rocznik(
            rozsz,
            waskie,
            szerokie,
            prompter,
            view,
            licznik_waski=licznik_waski,
            licznik_szeroki=licznik_szeroki,
        )
        if wybrane is None:
            return "anuluj", FetchPlan(criteria=criteria, count=0, threshold=threshold)
        criteria = wybrane
        # `wybrane` jest jednym z dwóch obiektów, które właśnie podaliśmy — porównanie
        # tożsamości mówi wprost, którą populację policzono, bez rekonstruowania jej z pola.
        count = licznik_szeroki if wybrane is szerokie else licznik_waski
    else:
        count = count_hits(criteria, deps)
    est = estimate(count, deps.profile, max_rekordow=criteria.max_rekordow)
    view.block(texts.cost_table(est, threshold=threshold, capped=criteria.max_rekordow is not None))
    if count == 0:
        view.message("Brak firm spełniających kryteria.")
        return "wyjdz", FetchPlan(criteria=criteria, count=0, threshold=threshold, estimate=est)

    if count > threshold and criteria.max_rekordow is None:
        return _over_threshold(criteria, deps, prompter, view, count, est, threshold, today)

    answer = cast(Decision, prompter.ask(_co_dalej_default(criteria)))
    if answer in ("popraw", "wyjdz"):
        return answer, FetchPlan(criteria=criteria, count=count, threshold=threshold, estimate=est)
    updated = criteria.model_copy(update={"szczegoly": answer == "szczegoly"})
    return answer, FetchPlan(criteria=updated, count=count, threshold=threshold, estimate=est)


def _co_dalej_default(criteria: Criteria) -> prompts.Question:
    default = "szczegoly" if criteria.szczegoly else "lista"
    return prompts.Question(
        id=prompts.CO_DALEJ.id,
        text=prompts.CO_DALEJ.text,
        options=prompts.CO_DALEJ.options,
        default=default,
    )


def _over_threshold(
    criteria: Criteria,
    deps: Deps,
    prompter: Prompter,
    view: View,
    count: int,
    est: Estimate,
    threshold: int,
    today: date | None,
) -> tuple[Decision, FetchPlan]:
    """Powyżej progu program nigdy nie startuje sam (UZUPELNIENIE_01 §C, scenariusz 9)."""
    plan = plan_batches(
        criteria, count, today=today or datetime.now(tz=UTC).date(), threshold=threshold
    )
    estimates = [
        estimate(plan.share, deps.profile, max_rekordow=criteria.max_rekordow) for _ in plan.batches
    ]
    view.block(texts.split_table(plan, estimates, szczegoly=criteria.szczegoly))
    answer = cast(Decision, prompter.ask(prompts.PODZIAL))
    return answer, FetchPlan(
        criteria=criteria, count=count, threshold=threshold, estimate=est, batches=plan
    )


def prepare_update(
    deps: Deps,
    prompter: Prompter,
    view: View,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[bool, UpdatePlan]:
    """Wycena aktualizacji i zgoda na nią — ten sam kształt, co przed pobieraniem.

    Zwraca (czy startować, plan). Kosztuje jedno tanie żądanie na okno pięciodniowe;
    domyślny zakres to jedno okno, więc zwykle jedno żądanie. Bez tego kroku `aktualizuj`
    ruszał od razu i operator dowiadywał się o trzydziestu minutach pracy z paska postępu."""
    plan = plan_update(deps, since=since, until=until)
    view.block(
        texts.update_cost_table(
            count=plan.count,
            requests=plan.requests(deps.profile),
            seconds=plan.seconds(deps.profile),
            since=plan.since,
            until=plan.until,
            windows=plan.windows,
        )
    )
    if plan.count == 0:
        return False, plan
    return prompter.confirm(prompts.AKTUALIZOWAC, default=True), plan


# ----------------------------------------------------------------------------- wykonanie


@dataclass(frozen=True)
class ExecuteResult:
    run_ids: tuple[str, ...]
    records: int
    notes: tuple[str, ...] = ()


def execute(
    decision: Decision, plan: FetchPlan, deps: Deps, view: View, *, force_lock: bool = False
) -> ExecuteResult:
    """Uruchamia decyzję podjętą w `prepare_fetch`. Bez pytań — decyzje zapadły wcześniej.

    Pisze natomiast na ekran: podział na partie melduje każdą z nich i kończy tabelą
    (`_run_batches`). Docstring mówił „bez wypisywania tabel", co było nieprawdą od czasu
    partii — a to jest zdanie, które czyta następna osoba, decydując, gdzie dopisać krok po
    pobraniu.

    `force_lock` przejmuje blokadę bazy po procesie, który nie zdążył jej zwolnić (ubity
    w połowie strony). Domyślnie fałsz — kreator nie ma tej flagi, bo nie ma jak potwierdzić,
    że tamten proces naprawdę nie żyje.

    Niespójny plan zgłaszamy wyjątkiem, a nie `assert` — asercje znikają pod `python -O`."""
    if decision == "wznow":
        if plan.resumable is None:
            raise ConfigError("Brak pobrania do wznowienia.")
        result = run_fetch(
            plan.criteria, deps, resume_run_id=plan.resumable.run_id, force_lock=force_lock
        )
        return _from_run(result)
    if decision == "raport":
        if plan.report is None:
            raise ConfigError("Brak raportu do pobrania.")
        result = run_report_fetch(plan.criteria, deps, plan.report, force_lock=force_lock)
        return _from_run(result)
    if decision == "partie":
        if plan.batches is None:
            raise ConfigError("Brak planu partii — powtórz wybór kryteriów.")
        return _run_batches(
            plan.batches, deps, view, threshold=plan.threshold, force_lock=force_lock
        )
    if decision in ("lista", "szczegoly"):
        result = run_fetch(
            plan.criteria, deps, known_count=plan.count or None, force_lock=force_lock
        )
        return _from_run(result)
    raise ConfigError(f"Decyzja {decision!r} nie uruchamia pobierania.")


def _from_run(result: RunResult) -> ExecuteResult:
    # `unresolved` musi mieć czytelnika także tutaj. Liczony był od początku, ale czytało go
    # wyłącznie podsumowanie `aktualizuj` — więc operator, któremu `pobierz --szczegoly`
    # zgubiłby część wpisów, nie zobaczyłby nic. Cichy ubytek na jednej ścieżce, wykryty na
    # drugiej, to ten sam defekt, a nie inny.
    notes = [texts.unresolved_note(result.unresolved)] if result.unresolved else []
    return ExecuteResult(run_ids=(result.run_id,), records=result.records, notes=tuple(notes))


def _run_batches(
    plan: BatchPlan, deps: Deps, view: View, *, threshold: int, force_lock: bool = False
) -> ExecuteResult:
    rows: list[tuple[str, str, int, int]] = []
    total = len(plan.batches)

    def on_batch(outcome: BatchOutcome) -> None:
        rows.append((outcome.label, outcome.status, outcome.count, outcome.records))
        view.message(f"Partia {len(rows)}/{total} — {outcome.label}: {outcome.status}.")

    result: BatchResult = run_batched_fetch(
        plan, deps, threshold=threshold, on_batch=on_batch, force_lock=force_lock
    )
    view.block(texts.batches_table(rows))
    notes: list[str] = []
    if result.missing:
        notes.append(texts.batches_shortfall(result.counted, result.expected, result.missing))
    if result.surplus:
        notes.append(texts.batches_surplus(result.counted, result.expected, result.surplus))
    return ExecuteResult(run_ids=result.run_ids, records=result.records, notes=tuple(notes))


# ----------------------------------------------------------------------------- eksport


def export_and_report(
    run_ids: Sequence[str],
    deps: Deps,
    view: View,
    *,
    out: Path | None = None,
    name_from: Criteria | None = None,
    cel: str | None = None,
    formats: Sequence[str] = ("xlsx",),
    notes: Sequence[str] = (),
) -> ExportSummary:
    """Eksport z bazy plus podsumowanie końcowe — te same zdania w każdym wejściu.

    `name_from` nadaje nazwę pliku z pierwotnych kryteriów; przy pobraniu w partiach
    nazwa z pierwszego runu opisywałaby tylko pierwszą partię."""
    if not run_ids:
        # Pobranie w partiach, w którym każda partia okazała się pusta, nie zakłada żadnego
        # runu. Bez tej furtki nazwa pliku sięgnęłaby po `run_ids[0]` i wysypała się.
        view.message("Żadna partia nie zwróciła rekordów — nie ma czego eksportować.")
        return ExportSummary(
            paths=(), records=0, by_status={}, with_phone=0, with_email=0, sheets=()
        )
    dest = _destination(run_ids, deps, out, name_from)
    summary = run_export(list(run_ids), dest, deps, cel_pobrania=cel, formats=formats)
    view.block(
        texts.summary_table(
            SummaryInput(
                paths=summary.paths,
                records=summary.records,
                by_status=summary.by_status,
                with_phone=summary.with_phone,
                with_email=summary.with_email,
                sheets=summary.sheets,
                kind=summary.kind,
                run_ids=summary.run_ids,
                statuses=summary.statuses,
                log_path=deps.settings.log_dir / LOG_FILE_NAME,
                extra=tuple(("uwaga", note) for note in notes),
            )
        )
    )
    return summary


def _destination(
    run_ids: Sequence[str], deps: Deps, out: Path | None, name_from: Criteria | None
) -> Path:
    if out is not None:
        return out
    if name_from is not None:
        moment = datetime.now(tz=UTC)
        return deps.settings.output_dir / output_name(
            name_from, deps.settings.environment, moment, demo=deps.demo
        )
    return default_export_path(deps, run_ids[0])
