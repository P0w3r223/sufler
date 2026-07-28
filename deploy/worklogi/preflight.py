"""Preflight drzwi kart czasu (live-smoke poz. 17, ADR 0035) — READ-ONLY, bez Graph/Teams.

Zbiera w jednym poleceniu to, czego pakiet ``pytest`` nie sprawdza wobec KONKRETNEGO ``.env``:
spójność konfiguracji (``WorklogiSettings.validate`` — ścieżki poza repo/danymi, mapa tożsamości,
źródło godzin), stan DWÓCH bramek trybu bojowego (``dry_run`` + ``headers_confirmed``) oraz dowód,
że strażnik nagłówków NAPRAWDĘ blokuje ``DRY_RUN=false`` bez potwierdzenia. Dodatkowo generuje
PRZYKŁADOWY arkusz WorklogPRO z czystego rdzenia (bez sieci) — artefakt do porównania nagłówków
z szablonem z kreatora (Apps -> WorklogPRO -> Import worklogs, Krok 0 bramki).

Nic nie wysyła, nie loguje się device-code i NIE rusza realnego stanu ani żadnego konta Teams —
prawdziwy przebieg (``uv run workmate-worklogi --once``) pozostaje krokiem operatora, bo dotyka
Graph (``fetch_team_members``) i w trybie bojowym prywatnych wiadomości.

Uruchomienie (w środowisku projektu, z wypełnionym ``.env``)::

    uv run --no-sync python deploy/worklogi/preflight.py
    uv run --no-sync python deploy/worklogi/preflight.py --sample D:/worklogi/przyklad.xlsx

Kody wyjścia: ``0`` = konfiguracja spójna i bramka nagłówków trzyma; ``1`` = odrzucona
konfiguracja albo bramka nie zadziałała (szczegóły na stderr).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import date
from zoneinfo import ZoneInfo

from workmate.adapters.inbound import env
from workmate.config import Settings, WorklogiSettings
from workmate.core.domain.timesheet import Person, WorkEntry, build_timesheet
from workmate.core.domain.timesheet_sheet import (
    WORKLOGPRO_HEADERS,
    Sheet,
    project_sheet,
    sheet_filename,
)

# Reprezentatywny tydzień ZAMKNIĘTY (pon.-ndz., okno półotwarte) — stały, żeby próbka była
# powtarzalna. Nie zależy od zegara: to tylko materiał do obejrzenia kolumn, nie realny przebieg.
_SAMPLE_WEEK_START = date(2026, 7, 13)
_SAMPLE_WEEK_END = date(2026, 7, 20)
_SAMPLE_TZ = "Europe/Warsaw"
_SAMPLE_START_HOUR = 8


def _log(msg: str = "") -> None:
    """Wypis na stderr — stdout zostaje czysty (spójnie z konwencją narzędzi deploy)."""
    print(msg, file=sys.stderr)


def _report_config(settings: WorklogiSettings) -> None:
    """Wypisz stan konfiguracji drzwi — jednym rzutem oka widać, co jest uzbrojone."""
    _log("[cfg] drzwi kart czasu (ADR 0035):")
    _log(f"    enabled           = {settings.enabled}")
    _log(f"    dry_run           = {settings.dry_run}")
    _log(f"    headers_confirmed = {settings.headers_confirmed}")
    _log(f"    hours_source      = {settings.hours_source!r}")
    _log(f"    output_dir        = {settings.output_dir}")
    _log(f"    identities        = {settings.identities_path}")
    _log(f"    team_id           = {settings.team_id or '(brak)'}")
    _log(f"    only_source_ids   = {settings.only_source_ids or '(wszyscy)'}")
    _log(f"    enable_attachment = {settings.enable_attachment}")


def _check_validate(settings: WorklogiSettings) -> bool:
    """Uruchom prawdziwą walidację startu wobec tego ``.env``; zwróć czy przeszła.

    Przy ``enabled=false`` walidacja sprawdza tylko zakresy liczbowe/strefę (świadomie — literówka
    nie ma spać do dnia przełączenia bramki). Przy ``enabled=true`` egzekwuje pełny komplet:
    ścieżki poza repo i katalogiem danych, istnienie mapy tożsamości, źródło godzin.
    """
    data_dir = Settings.from_env().data_dir
    try:
        settings.validate(data_dir=data_dir)
    except ValueError as exc:
        _log(f"\n[validate] ODRZUCONO: {exc}")
        return False
    scope = "pełna (enabled=true)" if settings.enabled else "zakresy (enabled=false)"
    _log(f"\n[validate] OK — walidacja {scope} przeszła.")
    return True


def _check_headers_gate(settings: WorklogiSettings) -> bool:
    """Dowód NA ŻYWO, że ``DRY_RUN=false`` bez ``HEADERS_CONFIRMED=true`` NIE wystartuje.

    To jedyna ochrona przed rozesłaniem kilkunastu arkuszy z niepotwierdzonymi nagłówkami
    (WorklogPRO dopasowuje kolumny po nazwie). Sprawdzamy realny strażnik z ``config.py`` na
    klonie ustawień z wymuszonym trybem bojowym — nie duplikujemy tu jego logiki.
    """
    battle = replace(settings, dry_run=False, headers_confirmed=False)
    try:
        battle._validate_headers_confirmed()
    except ValueError:
        _log("[gate] OK: DRY_RUN=false bez HEADERS_CONFIRMED=true jest blokowane (strażnik żyje).")
        return True
    _log("[gate] !! Strażnik nagłówków NIE zadziałał — tryb bojowy ruszyłby bez potwierdzenia!")
    return False


def _build_sample_sheet() -> tuple[Sheet, str]:
    """Zbuduj przykładowy arkusz z czystego rdzenia (bez Graph/Teams) i jego nazwę pliku."""
    person = Person(
        source_id="EMP-017",
        aad_user_id="00000000-0000-0000-0000-000000000000",
        jira_user="pilot@example.test",
        display_name="Pilot Przykladowy",
    )
    entries = [
        WorkEntry(source_id="EMP-017", day=date(2026, 7, 13), issue_key="WT-12", minutes=210),
        WorkEntry(
            source_id="EMP-017",
            day=date(2026, 7, 14),
            issue_key="WT-14",
            minutes=90,
            comment="przeglad kodu i poprawki",
        ),
        WorkEntry(source_id="EMP-017", day=date(2026, 7, 16), issue_key="WT-12", minutes=180),
    ]
    timesheet = build_timesheet(
        person,
        entries,
        week_start=_SAMPLE_WEEK_START,
        week_end=_SAMPLE_WEEK_END,
        week_label="2026-W29",
    )
    sheet = project_sheet(
        timesheet, start_hour=_SAMPLE_START_HOUR, tz=ZoneInfo(_SAMPLE_TZ)
    )
    return sheet, sheet_filename(timesheet)


def _print_sample(sheet: Sheet, filename: str) -> None:
    """Wypisz nagłówki i wiersze próbki na stderr — porównanie kolumn bez otwierania Excela."""
    _log(f"\n[sample] przykladowy arkusz ({filename}):")
    _log(f"    nagłówki: {list(sheet.headers)}")
    for row in sheet.rows:
        _log(f"    wiersz  : {list(row)}")
    _log(
        "  Porównaj nagłówki z szablonem z kreatora (Apps -> WorklogPRO -> Import worklogs). "
        "Rozjazd = popraw WORKLOGPRO_HEADERS i test test_headers_are_not_changed_by_accident."
    )


def _write_sample(sheet: Sheet, path: str) -> None:
    """Zrzuć próbkę do ``.xlsx`` (wymaga extra 'worklogi') — plik do porównania z szablonem."""
    from workmate.adapters.outbound.openpyxl_sheet_writer import OpenpyxlSheetWriter

    OpenpyxlSheetWriter().write(path, sheet.headers, sheet.rows)
    _log(f"[sample] zapisano arkusz: {path}")


def _run(sample_path: str | None) -> int:
    env.load_dotenv()
    settings = WorklogiSettings.from_env()
    _report_config(settings)

    ok = _check_validate(settings)
    _log()
    gate_ok = _check_headers_gate(settings)
    _log(f"[headers] hipoteza WORKLOGPRO_HEADERS = {list(WORKLOGPRO_HEADERS)}")

    sheet, filename = _build_sample_sheet()
    _print_sample(sheet, filename)
    if sample_path:
        _write_sample(sheet, sample_path)

    if ok and gate_ok:
        _log("\nPreflight OK — config spójny, bramka nagłówków trzyma. Krok 0 (szablon) + ")
        _log("przebieg próbny (uv run workmate-worklogi --once) = kroki operatora.")
        return 0
    _log("\nPreflight NIEUDANY — popraw powyższe przed uruchomieniem drzwi.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="worklogi-preflight",
        description="Read-only preflight drzwi kart czasu (live-smoke poz. 17, ADR 0035).",
    )
    parser.add_argument(
        "--sample",
        metavar="PATH",
        default=None,
        help="Zapisz przykładowy arkusz WorklogPRO do wskazanego .xlsx (poza repo!).",
    )
    args = parser.parse_args(argv)
    return _run(args.sample)


if __name__ == "__main__":
    raise SystemExit(main())
