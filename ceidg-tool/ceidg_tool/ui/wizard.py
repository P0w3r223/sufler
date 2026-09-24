"""Sesja interaktywna: pierwszy ekran, menu i obsługa pięciu działań z uzupelnienie-01.md §A.

Kreator nie podejmuje własnych decyzji o pobieraniu — cała kolejność (wznowienie, raport,
`count`, tabela kosztów, podział na partie) siedzi w `flow`, wspólna z flagami.
Środowisko i zgoda na produkcję są rozstrzygnięte przed wejściem tutaj i kreator ich nie zmienia.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pydantic import ValidationError

from ..criteria import Criteria, bledy_po_polsku
from ..errors import CeidgError, ConfigError
from ..pipeline import (
    Deps,
    RunResult,
    list_resumable,
    lookup_nip,
    run_fetch,
    run_update,
)
from . import flow, prompts, texts
from .flow import View
from .prompts import Prompter

MENU_EXIT = "wyjdz"


def _menu_items(deps: Deps) -> tuple[texts.MenuItem, ...]:
    """Wznowienie jest pierwsze, gdy w bazie czeka niedokończone zadanie (§A punkt 4)."""
    items: list[texts.MenuItem] = []
    unfinished = list_resumable(deps)
    if unfinished:
        newest = unfinished[0]
        items.append(
            texts.MenuItem(
                "wznow",
                "Wznowić przerwane pobieranie",
                f"{newest.run_id[:8]}…, {newest.records_seen} rekordów",
            )
        )
    items.extend(
        (
            texts.pobierz_menu_item(deps.assistant is not None),
            texts.MenuItem("aktualizuj", "Zaktualizować bazę o zmiany", "od ostatniego pobrania"),
            texts.MenuItem("raport", "Pobrać gotowy raport", "region i okres, 2 zapytania"),
            texts.MenuItem("nip", "Sprawdzić pojedynczą firmę po NIP", "2 zapytania"),
            texts.MenuItem(MENU_EXIT, "Wyjść", ""),
        )
    )
    return tuple(items)


def _menu_question(items: Sequence[texts.MenuItem]) -> prompts.Question:
    return prompts.Question(
        id=prompts.MENU.id,
        text=prompts.MENU.text,
        options=tuple(prompts.Option(str(i), item.label) for i, item in enumerate(items, start=1)),
        default="1",
    )


def run_wizard(deps: Deps, prompter: Prompter, view: View, *, version: str) -> int:
    """Pętla menu. Błąd jednego działania wraca do menu, a nie kończy sesji."""
    flow.show_first_screen(view, deps, version=version)
    while True:
        items = _menu_items(deps)
        view.block(texts.menu_block(items))
        try:
            choice = items[int(prompter.ask(_menu_question(items))) - 1]
        except prompts.CancelledError:
            # Ctrl+C w menu to rezygnacja, a nie błąd konfiguracji — kod wyjścia jak przy „Wyjść”.
            view.message("Przerwano — kończę sesję.")
            return 0
        except ConfigError as exc:
            # Zła odpowiedź w trybie awaryjnym (`input` zamiast strzałek) to pomyłka przy
            # menu, nie błąd konfiguracji — pytamy jeszcze raz zamiast kończyć sesję.
            view.error(str(exc))
            continue
        if choice.key == MENU_EXIT:
            return 0
        try:
            _dispatch(choice.key, deps, prompter, view)
        except prompts.CancelledError as exc:
            # Sam powód, bez domysłów o tym, co zdążyło się wydarzyć: przerwanie przy
            # kryteriach nie zostawia żadnego runu, więc podpowiedź o eksporcie byłaby
            # nieprawdą. Ścieżki, po których run istnieje, mówią to u siebie i z jego `id`.
            view.message(str(exc))
        except CeidgError as exc:
            view.error(str(exc))
        except OSError as exc:
            # Zapis pliku może się nie udać (brak miejsca, katalog tylko do odczytu);
            # sesja ma wtedy wrócić do menu, a nie skończyć się śladem stosu.
            view.error(f"Operacja na pliku się nie powiodła: {exc}")


def _dispatch(key: str, deps: Deps, prompter: Prompter, view: View) -> None:
    if key == "wznow":
        handle_resume(deps, prompter, view)
    elif key == "pobierz":
        handle_fetch(deps, prompter, view, source="auto")
    elif key == "raport":
        handle_fetch(deps, prompter, view, source="raport")
    elif key == "aktualizuj":
        handle_update(deps, prompter, view)
    elif key == "nip":
        handle_nip(deps, prompter, view)
    else:  # pragma: no cover - menu buduje tylko znane klucze
        raise ConfigError(f"Nieznane działanie {key!r}.")


# ----------------------------------------------------------------------------- kryteria


def collect_criteria(prompter: Prompter, view: View, deps: Deps | None = None) -> Criteria:
    """Pyta o kryteria, aż przejdą walidację `Criteria` — jedyny kontrakt wejścia.

    Po każdej nieudanej próbie pyta, czy powtarzać; bez tego operator, który przeklikał
    puste odpowiedzi, nie miałby jak wrócić do menu inaczej niż zabijając proces."""
    # Opis zdaniem jako **pierwsze** pytanie, nie szósta pozycja menu (ADR-0011, decyzja 5):
    # menu miałoby wtedy siedem pozycji przy pokazanym wznowieniu, a asystent byłby osobnym
    # działaniem z własną kopią ścieżki po kryteriach. Pusta odpowiedź przechodzi do dzisiejszych
    # ośmiu pytań **bez zmian**, więc operator bez klucza nie widzi żadnej różnicy.
    if deps is not None and deps.assistant is not None:
        opis = prompter.text(prompts.OPIS).strip()
        if opis:
            kryteria = flow.collect_from_description(deps, prompter, view, opis=opis)
            if kryteria is not None:
                view.block(texts.criteria_block(kryteria))
                return kryteria

    view.block(texts.hints_block())
    while True:
        answers = prompts.CriteriaAnswers()
        for question in prompts.criteria_questions():
            raw = prompter.text(question)
            if question.id in ("data_od", "data_do", "max_rekordow"):
                answers.set_scalar(question.id, raw)
            else:
                answers.set_list(question.id, raw)
        try:
            criteria = Criteria.model_validate(answers.values)
        except ValidationError as exc:
            view.error(f"Niepoprawne kryteria:\n{bledy_po_polsku(exc)}")
            _retry_or_cancel(prompter)
            continue
        if criteria.is_empty():
            view.error("Bez żadnego filtra zapytanie objęłoby cały rejestr — podaj kryterium.")
            _retry_or_cancel(prompter)
            continue
        view.block(texts.criteria_block(criteria))
        return criteria


def _retry_or_cancel(prompter: Prompter) -> None:
    if not prompter.confirm(prompts.PONOW_KRYTERIA, default=True):
        raise prompts.CancelledError("Rezygnacja z kryteriów — wracam do menu.")


def show_repeat_command(criteria: Criteria, deps: Deps, view: View) -> None:
    """Gotowe polecenie odtwarzające te kryteria — do powtórzeń i do harmonogramu.

    Zastąpiło pytanie „zapisać jako plik zapytania YAML?" (ADR-0022). Plik był jedyną drogą do
    harmonogramu, dopóki część pól nie miała flag; od 2026-09-10 ma je każde, więc plik został
    formatem, którego operator musiał się uczyć po to, żeby zrobić to samo. Polecenie pokazuje
    się **bez pytania**: nie kosztuje żądania ani pliku, więc pytanie było tylko kliknięciem.
    """
    view.block(texts.repeat_command_block(criteria, demo=deps.demo))


# ----------------------------------------------------------------------------- działania


def handle_fetch(deps: Deps, prompter: Prompter, view: View, *, source: str) -> None:
    criteria = collect_criteria(prompter, view, deps)
    while True:
        decision, plan = flow.prepare_fetch(criteria, deps, prompter, view, source=source)
        if decision == "popraw":
            criteria = collect_criteria(prompter, view, deps)
            continue
        # Polecenie **po** decyzjach, nie przed. Do 2026-09-07 kreator proponował powtórzenie
        # zaraz po zebraniu kryteriów, więc pokazywał je sprzed wyboru rocznika PKD i sprzed
        # wyboru „lista czy szczegóły" — a harmonogram uruchamiał wtedy inne zapytanie niż to,
        # które operator przed chwilą zatwierdził. `plan.criteria` niesie obie decyzje.
        if decision == "anuluj":
            # Operator odmówił podjęcia decyzji o roczniku, więc nie ma czego powtarzać —
            # polecenie niosłoby wybór, którego właśnie nie dokonał. `wyjdz` to co innego: tam
            # kryteria są już rozstrzygnięte, tylko koszt okazał się za wysoki.
            return
        # Polecenie **przed** „wyjdź", a nie po nim: zebranie kryteriów, zapisanie polecenia
        # i wyjście bez pobierania jest sensownym przejściem — tak buduje się wpis do
        # harmonogramu, nie płacąc za dane.
        show_repeat_command(plan.criteria, deps, view)
        if decision == "wyjdz":
            return
        cel = prompter.text(prompts.CEL)  # uzupelnienie-01.md §B: pole „cel pobrania” w Metadanych
        result = flow.execute(decision, plan, deps, view)
        flow.export_and_report(
            result.run_ids,
            deps,
            view,
            name_from=plan.criteria if len(result.run_ids) > 1 else None,
            cel=cel or None,
            notes=result.notes,
        )
        return


def handle_resume(deps: Deps, prompter: Prompter, view: View) -> None:
    unfinished = list_resumable(deps)
    if not unfinished:
        view.message(texts.NO_RESUMABLE)
        return
    run = unfinished[0]
    try:
        criteria = Criteria.model_validate_json(run.criteria_json)
    except ValidationError as exc:
        raise ConfigError(f"Run {run.run_id} ma nieczytelne kryteria: {exc}") from exc
    view.message(f"Wznawiam {run.run_id}: {criteria.describe()}")
    # Cel pytamy przed pobraniem, nie po: wznowienie potrafi trwać godziny, a przerwanie
    # pytania po jego zakończeniu zostawiłoby gotowy run bez skoroszytu — i bez pozycji
    # w menu, bo skończony run wypada z listy do wznowienia.
    cel = prompter.text(prompts.CEL)
    result = run_fetch(criteria, deps, resume_run_id=run.run_id)
    flow.export_and_report((result.run_id,), deps, view, cel=cel or None)


def handle_update(
    deps: Deps,
    prompter: Prompter,
    view: View,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> None:
    """Zakres domyślnie ze znacznika; `since`/`until` podaje wołający.

    Kreator sam o zakres nie pyta, ale przekazanie go dalej jest tym, co pozwala
    poprowadzić kreatora z zewnątrz po domkniętym, powtarzalnym zakresie — inaczej
    „teraz" wchodzi do środka i ten sam przebieg dwa razy znaczy co innego."""
    start, plan = flow.prepare_update(deps, prompter, view, since=since, until=until)
    if not start:
        view.message(texts.update_declined(plan.count))
        return
    result: RunResult = run_update(deps, since=plan.since, until=plan.until)
    view.message(
        texts.update_summary(
            result.records, result.details, result.unresolved, result.stale_details
        )
    )
    if result.records:
        _export_on_request(deps, prompter, view, result.run_id)


def handle_nip(deps: Deps, prompter: Prompter, view: View) -> None:
    """Sprawdzenie po NIP jest podglądem — plik z danymi osobowymi powstaje tylko na życzenie,
    tak samo jak w `sprawdz-nip`, gdzie zapisu wymaga jawne `--out`."""
    nip = prompter.text(prompts.NIP)
    lookup = lookup_nip(nip, deps)
    view.block(texts.firm_card(lookup.record, nip=lookup.nip))
    if lookup.record is not None:
        _export_on_request(deps, prompter, view, lookup.run_id)


def _export_on_request(deps: Deps, prompter: Prompter, view: View, run_id: str) -> None:
    """Zapis po zakończonym pobraniu. Tu pytanie pada **po** żądaniach, więc przerwanie
    zostawia gotowy run bez pliku — mówimy wtedy wprost, jak go odzyskać, z jego `id`,
    bo `eksportuj` bez `--run-id` wziąłby najnowszy run w bazie, niekoniecznie ten."""
    try:
        if not prompter.confirm(prompts.ZAPISZ_EXCEL, default=False):
            return
        cel = prompter.text(prompts.CEL)
    except prompts.CancelledError as exc:
        view.message(
            f"{exc} Pobrane dane zostają w bazie — skoroszyt zrobisz poleceniem "
            f"`ceidg-tool eksportuj --run-id {run_id}`."
        )
        return
    flow.export_and_report((run_id,), deps, view, cel=cel or None)
