"""Interfejs wiersza poleceń (typer): flagi, plik YAML i wejście do kreatora.

Cienki adapter — decyzje i teksty należą do `ui` (ADR-0008, reguła granic 9), żeby
flagi, plik zapytania, tryb `--tak` i kreator pokazywały dosłownie to samo.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from . import __version__
from .config import (
    KEYRING_ASSISTANT_USERNAME,
    KEYRING_USERNAME,
    Settings,
    delete_token_from_keyring,
    load_settings,
    safe_filename,
    store_token_in_keyring,
)
from .console import ConsoleEvents
from .criteria import Criteria
from .errors import CeidgError, ConfigError
from .logsetup import close_file_handlers, get_logger, setup_logging
from .pipeline import (
    Deps,
    build_deps,
    criteria_from_yaml,
    list_resumable,
    lookup_nip,
    purge_report_files,
    run_fetch,
    run_update,
)
from .richtext import make_console
from .ui import flow, texts, wizard
from .ui.prompts import (
    OverridePrompter,
    Prompter,
    confirm_line,
    interactive_available,
    is_yes,
    make_prompter,
)
from .ui.render import ConsoleView

app = typer.Typer(
    help="Pobieranie danych JDG z API v3 CEIDG do Excela.",
    no_args_is_help=False,
    add_completion=False,
    rich_markup_mode="rich",
)
token_app = typer.Typer(help="Zarządzanie tokenem w systemowym magazynie haseł.")
app.add_typer(token_app, name="token")
console = make_console()
view = ConsoleView(console)
log = get_logger("cli")

EnvOpt = Annotated[
    str | None, typer.Option("--srodowisko", "-s", help="test (domyślnie) albo prod")
]
ProdOpt = Annotated[
    bool, typer.Option("--produkcja", help="Zgoda na środowisko produkcyjne (dane osobowe)")
]
YesOpt = Annotated[bool, typer.Option("--tak", "-y", help="Tryb nieinteraktywny: decyzje domyślne")]
ForceOpt = Annotated[
    bool,
    typer.Option(
        "--force",
        help="Przejmij blokadę bazy po procesie, który jej nie zwolnił (np. został ubity)",
    ),
]


def _confirm(question: str, *, default: bool = False) -> bool:
    """Potwierdzenie po polsku, w tym samym rozumieniu co w kreatorze.

    `typer.confirm` przyjmuje wyłącznie `y`/`n`, więc operator, który na polskie pytanie
    odpowiadał „tak", dostawał je z powrotem. Te dwa potwierdzenia zostają poza protokołem
    `Prompter` (ADR-0008: zgoda na produkcję zapada przed powstaniem warstwy `ui`), ale
    rozumienie odpowiedzi jest wspólne — inaczej „tak" znaczyłoby co innego w kreatorze
    i co innego w wierszu poleceń.
    """
    try:
        answer = typer.prompt(
            confirm_line(question, default=default), default="", show_default=False
        )
    except typer.Abort as exc:
        # Ctrl+C i koniec wejścia dawały angielskie „Aborted." i kod 1; kreator na tę samą
        # sytuację odpowiada po polsku i kodem 3, więc niech oba wejścia mówią to samo.
        raise ConfigError("Przerwano pytanie — nic nie zostało zrobione.") from exc
    return is_yes(answer, default=default)


def _fail(exc: CeidgError) -> None:
    view.error(str(exc))
    log.error("%s: %s", type(exc).__name__, exc)
    raise typer.Exit(code=exc.exit_code)


def _settings(environment: str | None, prod: bool, yes: bool) -> Settings:
    if environment and environment.strip().lower() == "prod" and not prod:
        if yes:
            raise ConfigError("Środowisko produkcyjne wymaga jawnej flagi --produkcja.")
        prod = _confirm(texts.CONFIRM_PROD)
    settings = load_settings(environment=environment, prod_consent=prod)
    setup_logging(settings.log_dir)
    for warning in settings.warnings:
        view.warning(warning)
    return settings


def _banner(settings: Settings) -> None:
    """Pierwszy ekran — ta sama treść co w kreatorze (UZUPELNIENIE_01 §A)."""
    view.block(texts.first_screen(settings, now=datetime.now(tz=UTC), version=__version__))


def _prompter(tak: bool, overrides: dict[str, str] | None = None) -> Prompter:
    interactive = interactive_available(
        stdin_tty=sys.stdin.isatty(), stdout_tty=sys.stdout.isatty(), yes=tak
    )
    base = make_prompter(yes=tak, interactive=interactive)
    return OverridePrompter(base, overrides) if overrides else base


def _sanitised(out: Path | None, deps: Deps) -> Path | None:
    """UZUPELNIENIE_01 §B: pliki wynikowe tylko w katalogu wyniki/, nazwa oczyszczona."""
    if out is None:
        return None
    return deps.settings.output_dir / safe_filename(out.stem, ".xlsx")


OpisOpt = Annotated[
    str | None,
    typer.Option("--opis", help="opisz zapytanie zdaniem; wymaga klucza asystenta"),
]


def _criteria_from_options(
    zapytanie: Path | None,
    wojewodztwo: list[str],
    miasto: list[str],
    powiat: list[str],
    gmina: list[str],
    nazwa: list[str],
    nip: list[str],
    regon: list[str],
    pkd: list[str],
    status: list[str],
    od: str | None,
    do: str | None,
    szczegoly: bool,
    maks: int | None,
) -> Criteria:
    if zapytanie is not None:
        base = criteria_from_yaml(zapytanie)
        overrides: dict[str, object] = {}
        if szczegoly:
            overrides["szczegoly"] = True
        if maks is not None:  # flaga CLI ma pierwszeństwo nad plikiem
            overrides["max_rekordow"] = maks
        return base.model_copy(update=overrides) if overrides else base
    try:
        return Criteria(
            wojewodztwo=tuple(wojewodztwo),
            miasto=tuple(miasto),
            powiat=tuple(powiat),
            gmina=tuple(gmina),
            nazwa=tuple(nazwa),
            nip=tuple(nip),
            regon=tuple(regon),
            pkd=tuple(pkd),
            status=tuple(status),  # type: ignore[arg-type]
            data_od=date.fromisoformat(od) if od else None,
            data_do=date.fromisoformat(do) if do else None,
            szczegoly=szczegoly,
            max_rekordow=maks,
        )
    except ValueError as exc:
        raise ConfigError(f"Niepoprawne kryteria: {exc}") from exc


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
) -> None:
    """Bez polecenia uruchamia kreator; poza terminalem pokazuje pomoc."""
    if ctx.invoked_subcommand is not None:
        return
    if not interactive_available(
        stdin_tty=sys.stdin.isatty(), stdout_tty=sys.stdout.isatty(), yes=False
    ):
        # `typer` w trybie `rich` drukuje pomoc sam i zwraca pusty napis; drukujemy tylko
        # wtedy, gdy jednak coś zwrócił, żeby nie dokładać pustego wiersza ani nie zgubić
        # pomocy, gdyby formatowanie kiedyś wróciło do zwykłego `click`.
        help_text = ctx.get_help()
        if help_text:
            view.message(help_text)
        return
    kreator(srodowisko=srodowisko, produkcja=produkcja)


@app.command()
def kreator(srodowisko: EnvOpt = None, produkcja: ProdOpt = False) -> None:
    """Prowadzi krok po kroku: pierwszy ekran, menu, kryteria, koszty, wynik."""
    try:
        settings = _settings(srodowisko, produkcja, False)
        if not interactive_available(
            stdin_tty=sys.stdin.isatty(), stdout_tty=sys.stdout.isatty(), yes=False
        ):
            raise ConfigError(
                "Kreator wymaga terminala. W harmonogramie użyj `pobierz --zapytanie plik.yaml "
                "--tak`."
            )
        events = ConsoleEvents(console)
        deps = build_deps(settings, events=events)
        for warning in deps.warnings:
            view.warning(warning)
        try:
            code = wizard.run_wizard(deps, _prompter(False), view, version=__version__)
        finally:
            events.close()
            deps.store.close()
        raise typer.Exit(code=code)
    except CeidgError as exc:
        _fail(exc)


@app.command("sprawdz-nip")
def sprawdz_nip(
    nip: Annotated[str, typer.Argument(help="NIP firmy, 10 cyfr")],
    out: Annotated[Path | None, typer.Option("--out", "-o")] = None,
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
    tak: YesOpt = False,
) -> None:
    """Sprawdza jedną firmę po NIP: suma kontrolna lokalnie, potem dwa zapytania."""
    try:
        settings = _settings(srodowisko, produkcja, tak)
        _banner(settings)
        events = ConsoleEvents(console, quiet=True)
        deps = build_deps(settings, events=events)
        for warning in deps.warnings:
            view.warning(warning)
        try:
            lookup = lookup_nip(nip, deps)
            view.block(texts.firm_card(lookup.record, nip=lookup.nip))
            if lookup.record is not None and out is not None:
                _export_after(deps, lookup.run_id, out, None, "xlsx")
        finally:
            events.close()
            deps.store.close()
    except CeidgError as exc:
        _fail(exc)


@app.command()
def pobierz(
    wojewodztwo: Annotated[
        list[str] | None, typer.Option("--wojewodztwo", "-w", help="np. podlaskie")
    ] = None,
    miasto: Annotated[list[str] | None, typer.Option("--miasto", "-m")] = None,
    powiat: Annotated[list[str] | None, typer.Option("--powiat")] = None,
    gmina: Annotated[list[str] | None, typer.Option("--gmina")] = None,
    nazwa: Annotated[list[str] | None, typer.Option("--nazwa", "-n", help="fragment nazwy")] = None,
    nip: Annotated[list[str] | None, typer.Option("--nip")] = None,
    regon: Annotated[list[str] | None, typer.Option("--regon")] = None,
    pkd: Annotated[list[str] | None, typer.Option("--pkd", help="np. 62.10.B")] = None,
    status: Annotated[
        list[str] | None, typer.Option("--status", help="AKTYWNY, ZAWIESZONY, …")
    ] = None,
    od: Annotated[str | None, typer.Option("--od", help="data rozpoczęcia od (YYYY-MM-DD)")] = None,
    do: Annotated[str | None, typer.Option("--do", help="data rozpoczęcia do (YYYY-MM-DD)")] = None,
    szczegoly: Annotated[
        bool, typer.Option("--szczegoly/--lista", help="pełne szczegóły firm")
    ] = False,
    maks: Annotated[int | None, typer.Option("--maks", help="maksymalna liczba rekordów")] = None,
    zapytanie: Annotated[
        Path | None, typer.Option("--zapytanie", "-z", help="plik YAML z kryteriami")
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="nazwa pliku .xlsx (zawsze w katalogu wyniki/)"),
    ] = None,
    cel: Annotated[
        str | None, typer.Option("--cel", help="cel pobrania (trafia do Metadane)")
    ] = None,
    zrodlo: Annotated[str, typer.Option("--zrodlo", help="auto | api | raport")] = "auto",
    formaty: Annotated[str, typer.Option("--format", help="xlsx,csv,jsonl")] = "xlsx",
    partie: Annotated[
        bool,
        typer.Option(
            "--partie", help="Zgoda na podział na partie po dacie, gdy trafień jest za dużo"
        ),
    ] = False,
    pkd_2007: Annotated[
        bool | None,
        typer.Option(
            "--pkd-2007/--bez-pkd-2007",
            help="Szukać też po odpowiednikach z PKD 2007 (okres przejściowy do 31.12.2026)",
        ),
    ] = None,
    opis: OpisOpt = None,
    force: ForceOpt = False,
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
    tak: YesOpt = False,
) -> None:
    """Pobiera firmy według kryteriów i zapisuje skoroszyt Excel."""
    try:
        settings = _settings(srodowisko, produkcja, tak)
        _banner(settings)
        criteria = _criteria_from_options(
            zapytanie,
            wojewodztwo or [],
            miasto or [],
            powiat or [],
            gmina or [],
            nazwa or [],
            nip or [],
            regon or [],
            pkd or [],
            status or [],
            od,
            do,
            szczegoly,
            maks,
        )
        if zrodlo not in KNOWN_SOURCES:
            raise ConfigError(f"Nieznane źródło {zrodlo!r}. Dozwolone: auto, api, raport.")
        _parse_formats(formaty)  # walidacja przed pobraniem, nie po nim
        events = ConsoleEvents(console)
        deps = build_deps(settings, events=events)
        for warning in deps.warnings:
            view.warning(warning)
        prompter = _prompter(tak, {"podzial": "partie"} if partie else None)
        if opis and tak:
            # Odmowa **przed** zapytaniem modelu, nie po nim. Program wie od początku, że tej
            # kombinacji nie da się potwierdzić, więc wydawanie na nią pieniędzy i siedmiu sekund
            # jest tą samą pomyłką, co żądanie do API na NIP z błędną sumą kontrolną: koszt
            # poniesiony po to, żeby dowiedzieć się czegoś, co było wiadome od razu.
            # Znalezisko z przebiegu B5 (2026-09-07).
            raise ConfigError(texts.ASSISTANT_NEEDS_A_HUMAN)
        if opis:
            # Ta sama implementacja, co w kreatorze — oba wejścia nie mogą się rozjechać.
            # W trybie `--tak` pytanie o zatwierdzenie ma `safe_default=False`, więc kończy się
            # kodem 3: harmonogram nie ma prawa działać na interpretacji, której nikt nie
            # przeczytał. Jego drogą jest plik YAML, zapisywany przez kreator z wyniku asystenta.
            z_opisu = flow.collect_from_description(deps, prompter, view, opis=opis)
            if z_opisu is None:
                raise ConfigError(texts.ASSISTANT_UNAVAILABLE)
            criteria = z_opisu
        if criteria.is_empty():
            raise ConfigError(texts.EMPTY_CRITERIA)
        view.block(texts.criteria_block(criteria))
        try:
            decision, plan = flow.prepare_fetch(
                criteria,
                deps,
                prompter,
                view,
                source=zrodlo,
                # Przy `--tak` pytanie i tak rozwiązałoby się odpowiedzią domyślną, więc drugie
                # zapytanie o `count` poszłoby na wiedzę, którą już mamy — ta sama pomyłka co
                # w przebiegu B5. Harmonogram zachowuje dzisiejsze zachowanie i dostaje o tym
                # jedno zdanie; szerzej wybiera się jawnie flagą.
                #
                # `and not criteria.pkd_2007`: plik zapytania z zapisanym wyborem **jest**
                # decyzją operatora i to on ma wygrać, a nie domyślne zawężenie wynikające
                # z trybu nieinteraktywnego. Bez tego warunku kanoniczne uruchomienie
                # z harmonogramu — plik plus `--tak` — odbijałoby się o sprzeczność.
                rocznik_2007=(
                    pkd_2007
                    if pkd_2007 is not None
                    else (False if tak and not criteria.pkd_2007 else None)
                ),
            )
            if decision in ("wyjdz", "anuluj"):
                return
            if decision == "popraw":
                view.message(texts.FIX_CRITERIA)
                return
            result = flow.execute(decision, plan, deps, view, force_lock=force)
            flow.export_and_report(
                result.run_ids,
                deps,
                view,
                out=_sanitised(out, deps),
                name_from=plan.criteria if len(result.run_ids) > 1 else None,
                cel=cel,
                formats=_parse_formats(formaty),
                notes=result.notes,
            )
        finally:
            events.close()
            deps.store.close()
    except CeidgError as exc:
        _fail(exc)


KNOWN_FORMATS = frozenset({"xlsx", "csv", "jsonl"})
KNOWN_SOURCES = frozenset({"auto", "api", "raport"})


def _parse_formats(formaty: str) -> tuple[str, ...]:
    formats = tuple(f.strip().lower() for f in formaty.split(",") if f.strip())
    unknown = sorted(set(formats) - KNOWN_FORMATS)
    if unknown or not formats:
        raise ConfigError(
            f"Nieznany format: {', '.join(unknown) or '(pusty)'}. Dozwolone: xlsx, csv, jsonl."
        )
    return formats


def _export_after(deps: Deps, run_id: str, out: Path | None, cel: str | None, formaty: str) -> None:
    flow.export_and_report(
        (run_id,),
        deps,
        view,
        out=_sanitised(out, deps),
        cel=cel,
        formats=_parse_formats(formaty),
    )


@app.command()
def wznow(
    run_id: Annotated[
        str | None, typer.Option("--run-id", help="domyślnie ostatni przerwany")
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", "-o")] = None,
    cel: Annotated[str | None, typer.Option("--cel")] = None,
    force: ForceOpt = False,
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
    tak: YesOpt = False,
) -> None:
    """Wznawia przerwane pobieranie z checkpointu i eksportuje wynik."""
    try:
        settings = _settings(srodowisko, produkcja, tak)
        _banner(settings)
        events = ConsoleEvents(console)
        deps = build_deps(settings, events=events)
        for warning in deps.warnings:
            view.warning(warning)
        try:
            if run_id is None:
                candidates = list_resumable(deps)
                if not candidates:
                    view.message(texts.NO_RESUMABLE)
                    return
                run_id = candidates[0].run_id
            run = deps.store.get_run(run_id)
            if run.kind != "firmy":
                raise ConfigError(
                    f"Run {run_id} to pobranie typu {run.kind!r}, nie da się go wznowić. "
                    "Powtórz `pobierz --zrodlo raport` albo `aktualizuj`."
                )
            try:
                criteria = Criteria.model_validate_json(run.criteria_json)
            except ValidationError as exc:
                raise ConfigError(f"Run {run_id} ma nieczytelne kryteria: {exc}") from exc
            result = run_fetch(criteria, deps, resume_run_id=run_id, force_lock=force)
            _export_after(deps, result.run_id, out, cel, "xlsx")
        finally:
            events.close()
            deps.store.close()
    except CeidgError as exc:
        _fail(exc)


@app.command()
def eksportuj(
    run_id: Annotated[
        str | None, typer.Option("--run-id", help="domyślnie ostatni zakończony")
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", "-o")] = None,
    cel: Annotated[str | None, typer.Option("--cel")] = None,
    formaty: Annotated[str, typer.Option("--format", help="xlsx,csv,jsonl")] = "xlsx",
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
) -> None:
    """Ponowny eksport z bazy — bez żadnego żądania do API."""
    try:
        settings = _settings(srodowisko, produkcja, True)
        # Pasek i komunikaty także tutaj. Bez `events` zależności dostawały `NullEvents`, więc
        # z `eksportuj` znikał nie tylko pasek, ale i zdanie „Zapisuję skoroszyt…" — a zapis
        # pełnego województwa to przy zmierzonych 453 firmach na sekundę ponad dziesięć minut
        # zupełnej ciszy, w poleceniu uruchamianym po każdym przerwanym pobraniu. Komentarz
        # nad tym zdaniem w `pipeline` obiecywał, że pada ono „nawet tam, gdzie paska nie
        # widać" — i była to jedyna droga, na której nie padało (audyt 2026-09-07).
        events = ConsoleEvents(console)
        deps = build_deps(settings, events=events, online=False)
        for warning in deps.warnings:
            view.warning(warning)
        try:
            if run_id is None:
                runs = deps.store.list_runs()
                if not runs:
                    view.message(texts.NO_RUNS)
                    return
                run_id = runs[0].run_id
            _export_after(deps, run_id, out, cel, formaty)
        finally:
            events.close()
            deps.store.close()
    except CeidgError as exc:
        _fail(exc)


@app.command()
def runy(srodowisko: EnvOpt = None, produkcja: ProdOpt = False) -> None:
    """Lista pobrań zapisanych w bazie."""
    try:
        settings = _settings(srodowisko, produkcja, True)
        deps = build_deps(settings, online=False)
        for warning in deps.warnings:
            view.warning(warning)
        try:
            rows: list[tuple[str, ...]] = []
            for run in deps.store.list_runs():
                try:
                    desc = Criteria.model_validate_json(run.criteria_json).describe()
                except ValueError:
                    desc = run.criteria_json[:60]
                rows.append(
                    (
                        run.run_id,
                        run.status,
                        run.mode,
                        str(run.records_seen),
                        str(run.count_api or ""),
                        run.created_utc,
                        desc[:70],
                    )
                )
            view.block(texts.runs_table(rows))
        finally:
            deps.store.close()
    except CeidgError as exc:
        _fail(exc)


@app.command()
def raporty(
    pobierz_id: Annotated[
        str | None, typer.Option("--pobierz", help="id raportu do pobrania")
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", "-o")] = None,
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
    tak: YesOpt = False,
) -> None:
    """Lista gotowych raportów CEIDG; opcjonalnie pobranie jednego (ZIP)."""
    try:
        settings = _settings(srodowisko, produkcja, tak)
        _banner(settings)
        # Pasek tylko przy pobieraniu: przy samej liście nie ma czego pokazywać, a przy
        # 21 MB archiwum cisza jest defektem (CLAUDE.md).
        events = ConsoleEvents(console, quiet=pobierz_id is None)
        deps = build_deps(settings, events=events)
        for warning in deps.warnings:
            view.warning(warning)
        try:
            client = deps.client
            assert client is not None
            reports = client.list_reports()
            if pobierz_id:
                match = [r for r in reports if r.id == pobierz_id]
                if not match:
                    raise ConfigError(f"Nie ma raportu o id {pobierz_id}.")
                dest = (
                    settings.data_dir
                    / "raporty"
                    / safe_filename(out.stem if out is not None else match[0].nazwa, ".zip")
                )
                client.download_report(match[0], dest, progress=deps.events.on_download)
                # Pasek gaśnie, zanim padnie zdanie o zapisie: żywy `rich` nadpisuje wszystko,
                # co wypisze się po nim (faza 3e). `close()` w `finally` powtórzy się nieszkodliwie.
                events.close()
                view.message(texts.report_saved(dest))
                return
            view.block(texts.reports_table(reports))
        finally:
            events.close()
            deps.store.close()
    except CeidgError as exc:
        _fail(exc)


@app.command()
def aktualizuj(
    od: Annotated[str | None, typer.Option("--od", help="początek zakresu zmian (ISO)")] = None,
    out: Annotated[Path | None, typer.Option("--out", "-o")] = None,
    force: ForceOpt = False,
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
    tak: YesOpt = False,
) -> None:
    """Pobiera zmiany od ostatniego uruchomienia (`/zmiana`) i odświeża cache szczegółów."""
    try:
        settings = _settings(srodowisko, produkcja, tak)
        _banner(settings)
        events = ConsoleEvents(console)
        deps = build_deps(settings, events=events)
        for warning in deps.warnings:
            view.warning(warning)
        try:
            try:
                parsed = datetime.fromisoformat(od) if od else None
                since = (
                    None
                    if parsed is None
                    else (parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC))
                )
            except ValueError as exc:
                raise ConfigError(f"--od musi być datą ISO (YYYY-MM-DD), jest: {od!r}") from exc
            prompter = _prompter(tak)
            start, plan = flow.prepare_update(deps, prompter, view, since=since)
            if not start:
                view.message(texts.update_declined(plan.count))
                return
            result = run_update(deps, since=plan.since, until=plan.until, force_lock=force)
            view.message(texts.update_summary(result.records, result.details, result.unresolved))
            if out:
                _export_after(deps, result.run_id, out, None, "xlsx")
        finally:
            events.close()
            deps.store.close()
    except CeidgError as exc:
        _fail(exc)


@app.command("sprawdz-token")
def sprawdz_token(srodowisko: EnvOpt = None, produkcja: ProdOpt = False) -> None:
    """Pokazuje środowisko, źródło i datę ważności tokenu — nic więcej."""
    try:
        settings = load_settings(environment=srodowisko, prod_consent=produkcja)
        view.message(texts.token_summary(settings, now=datetime.now(tz=UTC)))
        for warning in settings.warnings:
            view.warning(warning)
    except CeidgError as exc:
        _fail(exc)


AsystentOpt = Annotated[
    bool, typer.Option("--asystent", help="dotyczy klucza API asystenta, nie tokenu CEIDG")
]


@token_app.command("zapisz")
def token_zapisz(asystent: AsystentOpt = False) -> None:
    """Zapisuje token CEIDG albo (z `--asystent`) klucz API w magazynie haseł, bez echa."""
    pytanie = texts.ASSISTANT_KEY_PROMPT if asystent else texts.TOKEN_PROMPT
    sekret = typer.prompt(pytanie, hide_input=True).strip()
    if not sekret:
        view.message(texts.ASSISTANT_KEY_EMPTY if asystent else texts.TOKEN_EMPTY)
        raise typer.Exit(code=3)
    try:
        store_token_in_keyring(sekret, KEYRING_ASSISTANT_USERNAME if asystent else KEYRING_USERNAME)
    except CeidgError as exc:
        _fail(exc)
    view.message(texts.assistant_key_saved() if asystent else texts.token_saved())


@token_app.command("usun")
def token_usun(asystent: AsystentOpt = False) -> None:
    """Usuwa token CEIDG albo (z `--asystent`) klucz API z magazynu haseł."""
    try:
        removed = delete_token_from_keyring(
            KEYRING_ASSISTANT_USERNAME if asystent else KEYRING_USERNAME
        )
    except CeidgError as exc:
        _fail(exc)
    if asystent:
        view.message(texts.ASSISTANT_KEY_REMOVED if removed else texts.ASSISTANT_KEY_ABSENT)
    else:
        view.message(texts.token_removed(removed))


@app.command()
def wyczysc(
    starsze_niz: Annotated[
        int | None, typer.Option("--starsze-niz", help="dni; domyślnie retencja z konfiguracji")
    ] = None,
    wszystko: Annotated[
        bool,
        typer.Option("--wszystko", help="usuń bazę, checkpointy, logi i pobrane raporty"),
    ] = False,
    potwierdzam: Annotated[
        bool,
        typer.Option(
            "--potwierdzam-usuniecie",
            help="Zgoda na nieodwracalne usunięcie bazy w trybie --tak",
        ),
    ] = False,
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
    tak: YesOpt = False,
) -> None:
    """Usuwa stare dane z bazy (albo całą bazę z logami po potwierdzeniu)."""
    try:
        settings = _settings(srodowisko, produkcja, tak)
        if potwierdzam and not wszystko:
            raise ConfigError(
                "--potwierdzam-usuniecie dotyczy tylko --wszystko. Bez niego polecenie usuwa "
                "wyłącznie dane starsze niż retencja i żadnej zgody nie potrzebuje."
            )
        if wszystko:
            # Brak terminala to nie zgoda. Bez tego `wyczysc --wszystko` w harmonogramie kończył
            # się angielskim „Aborted." i kodem 1 zamiast czytelnym zdaniem i kodem 3.
            unattended = tak or not interactive_available(
                stdin_tty=sys.stdin.isatty(), stdout_tty=sys.stdout.isatty(), yes=False
            )
            if unattended and not potwierdzam:
                raise ConfigError(texts.purge_all_needs_consent(settings.store_path))
            if not unattended and not _confirm(texts.confirm_purge_all(settings.store_path)):
                raise typer.Exit(code=0)
            close_file_handlers()  # Windows nie usunie otwartego pliku logu
            targets = [
                settings.store_path,
                *settings.store_path.parent.glob(settings.store_path.name + "*"),
                *settings.log_dir.glob("*.log*"),
            ]
            failures: list[tuple[str, str]] = []
            for path in targets:
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    # Dalej, nie stop: zatrzymanie się na zajętym pliku bazy zostawiało raporty
                    # ZIP, czyli najobszerniejsze dane osobowe, na dysku bez słowa.
                    failures.append((str(path), str(exc)))
            zips = purge_report_files(settings.data_dir / "raporty", days=0, now_epoch=float("inf"))
            view.message(texts.purge_all_summary(zips))
            if failures:
                raise ConfigError(texts.purge_all_partial(failures))
            return
        deps = build_deps(settings, online=False)
        for warning in deps.warnings:
            view.warning(warning)
        try:
            days = starsze_niz or settings.retention_days
            runs, records = deps.store.purge_older_than(days)
            zips = purge_report_files(
                settings.data_dir / "raporty", days=days, now_epoch=datetime.now(tz=UTC).timestamp()
            )
            view.message(texts.purge_summary(runs, records, zips))
        finally:
            deps.store.close()
    except CeidgError as exc:
        _fail(exc)


def run() -> None:
    try:
        app()
    except KeyboardInterrupt:
        view.message(texts.INTERRUPTED)
        sys.exit(130)
