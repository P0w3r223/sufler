"""Punkt wejścia CLI: ``claude-summary`` — dzienne zestawienie promptów i commitów.

Orkiestracja: bramka zgody → rozwiąż zakres i źródła → sparsuj prompty (+ opcjonalnie commity)
→ pogrupuj po dniu → (opcjonalnie) opis prozą z LLM → wypisz Markdown/JSON i zapisz do pliku.
Parsowanie argumentów jest ręczne (konwencja WorkMate — bez argparse/click). Wejście/wyjście
wymuszamy na UTF-8, żeby polskie znaki nie psuły się w konsoli Windows.
"""

from __future__ import annotations

import contextlib
import sys
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from claude_summary.adapters import env, git_log, transcript_files
from claude_summary.adapters.anthropic_summarizer import AnthropicClient, summarize_day
from claude_summary.config import Settings
from claude_summary.core import render
from claude_summary.core.consent import grant_consent
from claude_summary.core.grouping import group_by_day
from claude_summary.core.models import Commit, DaySummary, SummaryReport
from claude_summary.core.ports import LlmClient

_FORMATS = ("md", "json", "both")
# Rozszerzenia, które sami dokładamy — tylko te wolno zdjąć z ``--out`` (``raport.2026-07-17``
# to nazwa pliku, nie sufiks formatu).
_OWN_SUFFIXES = (".md", ".json")

_USAGE = (
    "claude-summary — dzienne zestawienie pracy z historii promptów Claude Code i commitów.\n"
    "\n"
    "Użycie:\n"
    "  claude-summary --consent [--repo ŚCIEŻKA] [--author EMAIL] [--since RRRR-MM-DD]\n"
    "                 [--until RRRR-MM-DD] [--llm] [--format md|json|both] [--out PLIK]\n"
    "                 [--all-projects] [--project NAZWA_FOLDERU]\n"
    "\n"
    "Bez --repo używa wyłącznie historii promptów. Domyślny zakres: ostatnie 7 dni.\n"
)

_CONSENT_MSG = (
    "To narzędzie czyta Twoją PRYWATNĄ historię promptów Claude Code (~/.claude/projects).\n"
    "Uruchom ponownie z flagą --consent albo ustaw CLAUDE_SUMMARY_CONSENT=1, aby wyrazić zgodę."
)


def _force_utf8_io() -> None:
    """Wymuś UTF-8 na strumieniach — inaczej polskie znaki psują się w konsoli Windows."""
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8")


@dataclass
class _Args:
    repo: Path | None = None
    author: str | None = None
    since: date | None = None
    until: date | None = None
    consent: bool = False
    llm: bool = False
    all_projects: bool = False
    only_project: str | None = None
    fmt: str = "md"
    out: Path | None = None


def _parse_date(flag: str, value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"Flaga {flag}: zła data {value!r} (oczekiwano RRRR-MM-DD).") from exc


def _parse_args(argv: list[str]) -> _Args:
    args = _Args()
    index = 0

    def _value(flag: str) -> str:
        if index + 1 >= len(argv):
            raise SystemExit(f"Flaga {flag} wymaga wartości.\n\n{_USAGE}")
        return argv[index + 1]

    while index < len(argv):
        token = argv[index]
        if token in ("-h", "--help"):
            print(_USAGE)
            raise SystemExit(0)
        elif token == "--consent":
            args.consent = True
        elif token == "--llm":
            args.llm = True
        elif token in ("--all-projects", "--all"):
            args.all_projects = True
        elif token == "--repo":
            args.repo = Path(_value(token))
            index += 1
        elif token == "--author":
            args.author = _value(token)
            index += 1
        elif token == "--project":
            args.only_project = _value(token)
            index += 1
        elif token == "--since":
            args.since = _parse_date(token, _value(token))
            index += 1
        elif token == "--until":
            args.until = _parse_date(token, _value(token))
            index += 1
        elif token == "--out":
            args.out = Path(_value(token))
            index += 1
        elif token == "--format":
            args.fmt = _value(token)
            if args.fmt not in _FORMATS:
                allowed = ", ".join(_FORMATS)
                raise SystemExit(f"Flaga --format: dozwolone {allowed}, jest: {args.fmt!r}.")
            index += 1
        else:
            raise SystemExit(f"Nieznany argument: {token}\n\n{_USAGE}")
        index += 1
    return args


def _build_llm(settings: Settings) -> LlmClient | None:
    """Zbuduj klienta LLM albo ``None``, gdy brak klucza API (degradacja do samych danych)."""
    if not settings.api_key:
        return None
    return AnthropicClient(
        api_key=settings.api_key, model=settings.model, max_tokens=settings.max_tokens
    )


def _with_prose(days: list[DaySummary], *, settings: Settings, person: str) -> list[DaySummary]:
    """Dołóż opis prozą per dzień.

    Brak klucza API → pomiń warstwę (nota na stderr); błąd pojedynczego dnia → degraduj TEN dzień
    (opis ``None``), nie wywracając całego raportu.
    """
    client = _build_llm(settings)
    if client is None:
        print(
            "Uwaga: pomijam warstwę LLM — brak klucza API (ANTHROPIC_API_KEY / "
            "CLAUDE_SUMMARY_API_KEY). Zwracam same dane strukturalne.",
            file=sys.stderr,
        )
        return days
    result: list[DaySummary] = []
    for day in days:
        try:
            prose: str | None = summarize_day(day, person=person, llm=client)
        except Exception as exc:  # degradacja per dzień — jeden błąd nie wywraca całego raportu
            print(f"Uwaga: nie udało się opisać dnia {day.day} przez LLM: {exc}", file=sys.stderr)
            prose = None
        result.append(replace(day, llm_prose=prose))
    return result


def _write_files(
    report: SummaryReport, *, md: str | None, js: str | None, out: Path | None, settings: Settings
) -> None:
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        stem = out.with_suffix("") if out.suffix.lower() in _OWN_SUFFIXES else out
    else:
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        name = f"summary_{report.since.isoformat()}_{report.until.isoformat()}"
        stem = settings.output_dir / name
    for content, suffix in ((md, ".md"), (js, ".json")):
        if content is None:
            continue
        # Doklejamy sufiks tekstowo — ``with_suffix`` zjadłoby datę z ``raport.2026-07-17``.
        path = stem.with_name(stem.name + suffix)
        path.write_text(content, encoding="utf-8")
        print(f"Zapisano: {path}", file=sys.stderr)


def _emit(
    report: SummaryReport, *, tz: ZoneInfo, fmt: str, out: Path | None, settings: Settings
) -> None:
    md = render.to_markdown(report, tz=tz) if fmt in ("md", "both") else None
    js = render.to_json(report, tz=tz) if fmt in ("json", "both") else None
    print(js if fmt == "json" else md)
    if out is not None or fmt == "both":
        _write_files(report, md=md, js=js, out=out, settings=settings)


def run(args: _Args, settings: Settings) -> int:
    """Wykonaj bieg; zwróć kod wyjścia (0 sukces, 1 błąd walidacji/zgody)."""
    consent = grant_consent(flag=args.consent, env_consent=settings.consent)
    if consent is None:
        print(_CONSENT_MSG, file=sys.stderr)
        return 1

    tz = settings.tz()
    until = args.until or datetime.now(tz).date()
    since = args.since or (until - timedelta(days=settings.default_days - 1))
    if since > until:
        print(f"Błąd: --since ({since}) jest po --until ({until}).", file=sys.stderr)
        return 1

    repo = args.repo.resolve() if args.repo else None
    scan = transcript_files.scan_prompts(
        settings.projects_dir,
        consent=consent,
        repo=repo,
        all_projects=args.all_projects,
        only_project=args.only_project,
    )
    for warning in scan.warnings:  # pusty raport ma powiedzieć, dlaczego jest pusty
        print(f"Uwaga: {warning}", file=sys.stderr)
    prompts = list(scan.prompts)

    commits: list[Commit] = []
    person = (args.author or settings.author).strip()
    if repo is not None:
        author = (args.author or settings.author or git_log.resolve_author(repo)).strip()
        if not author:
            print(
                "Uwaga: nie ustalono autora (git config user.email pusty) — biorę wszystkie "
                "commity. Podaj --author, aby filtrować po osobie.",
                file=sys.stderr,
            )
        person = author or person
        commits = git_log.run_git_log(
            repo,
            since=f"{since.isoformat()} 00:00:00",
            until=f"{until.isoformat()} 23:59:59",
            author=author,
        )
    person = person or "nieznany"

    days = group_by_day(prompts, commits, tz=tz, since=since, until=until)
    if args.llm or settings.enable_llm:
        days = _with_prose(days, settings=settings, person=person)

    report = SummaryReport(
        person=person,
        since=since,
        until=until,
        repo=str(repo) if repo is not None else None,
        days=tuple(days),
    )
    _emit(report, tz=tz, fmt=args.fmt, out=args.out, settings=settings)
    return 0


def _settings_or_exit() -> Settings:
    """Wczytaj konfigurację; błąd zmiennych ``CLAUDE_SUMMARY_*`` to komunikat, nie traceback."""
    try:
        settings = Settings.from_env()
        settings.validate()
    except ValueError as exc:
        raise SystemExit(f"Błąd konfiguracji: {exc}") from exc
    return settings


def main() -> None:
    _force_utf8_io()
    # Argumenty PRZED konfiguracją: --help i błędna flaga mają działać także wtedy, gdy
    # zmienne środowiskowe są popsute (wcześniej wywalały się wcześniej, tracebackiem).
    args = _parse_args(sys.argv[1:])
    env.load_dotenv()
    raise SystemExit(run(args, _settings_or_exit()))


if __name__ == "__main__":
    main()
