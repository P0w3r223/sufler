"""Seed korpusu (W0) — ``workmate-seed-corpus`` importuje lokalne dokumenty do notatek.

Zimny start: świeży korpus jest pusty, więc bot nie ma czego przeszukać (zasila F3/F4/F5).
Ten entrypoint bierze katalog dokumentów (md/txt oraz docx/xlsx/pptx/pdf — te binarne ekstrahuje
do tekstu przez ``document_text``), wyprowadza z każdego DETERMINISTYCZNĄ notatkę i dokłada ją
przez SANKCJONOWANĄ ścieżkę zapisu (``NotesWriteService.save_note``) — nie nowe narzędzie
mutujące, więc golden MCP i ``NoteMetadata`` zostają nietknięte (reguły 2/3/6). Źródłem może być
folder zsynchronizowany z SharePointem (OneDrive) — importer nie sięga sam do sieci.

Dedup po id: id notatki wywodzi się ze STAŁYCH (projekt z zaufanego argumentu + data z nagłówka
``Date:`` albo ``--date`` + tytuł z pierwszego ``# H1``), więc powtórny przebieg wykrywa
istniejącą notatkę i ją POMIJA, zamiast tworzyć duplikat ``-2`` (pre-check ``exists`` na tym samym
id, które policzy ``save_note``).

Domyślnie DRY-RUN: pokazuje, co POWSTAŁOBY, nic nie zapisuje. Zapis dopiero z ``--write`` —
operator decyduje kiedy i dokąd, bo zmiana korpusu przesuwa ranking retrievalu i jest bramkowana
mikro-evalem (twarda reguła 9). ``derive_note``/``format_report`` są czyste i testowane; ``main``
to tylko I/O + kod wyjścia. Treść dokumentów to DANE — kopiujemy ją wiernie, nie interpretujemy.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from workmate.adapters.inbound.document_text import (
    SUPPORTED_EXTS,
    DocumentExtractionError,
    extract_text_from_path,
)
from workmate.config import Settings
from workmate.core.application.services import NotesWriteService, WriteError
from workmate.core.domain.models import NoteMetadata
from workmate.core.domain.paths import note_id as build_note_id
from workmate.core.ports.repositories import NotesWriter

# Data użyta, gdy dokument nie niesie własnego nagłówka ``Date:`` (np. README). Świadomie STAŁA,
# nie ``date.today()`` — dzień musi być powtarzalny, inaczej id zmieniałoby się co przebieg i
# dedup by nie działał.
_DEFAULT_FALLBACK_DATE = date(2025, 1, 1)
_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_DATE_RE = re.compile(r"^Date:\s*(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)

ACTION_SKIP = "pominięto (już istnieje)"
ACTION_WOULD_CREATE = "utworzyłbym"
ACTION_CREATED = "utworzono"
ACTION_COLLISION = "kolizja id w partii"
ACTION_ERROR = "błąd"


@dataclass(frozen=True)
class SeedResult:
    """Wynik dokumentu: źródło, docelowy id notatki, akcja i ewentualny szczegół błędu."""

    source: str
    note_id: str
    action: str
    detail: str = ""


def _humanize(stem: str) -> str:
    """Nazwa pliku bez rozszerzenia → tytuł: myślniki/podkreślenia na spacje, pierwsza wielka."""
    words = re.sub(r"[-_]+", " ", stem).strip()
    return words[:1].upper() + words[1:] if words else stem


def _kind_tag(source: str) -> str:
    """Tag rodzaju źródła: ``readme`` / ``adr`` / ``doc`` — z nazwy pliku, nie z treści."""
    stem = Path(source).stem.lower()
    if stem == "readme":
        return "readme"
    if "adr" in source.lower() or re.match(r"\d{4}", stem):
        return "adr"
    return "doc"


def derive_note(
    source: str, text: str, *, project: str, fallback_date: date
) -> tuple[NoteMetadata, str]:
    """Zbuduj (metadane, treść) z dokumentu — CZYSTO, deterministycznie z zawartości i argumentów.

    Tytuł: pierwszy ``# H1`` (fallback: uczłowieczona nazwa pliku). Data: nagłówek ``Date:``
    (fallback: ``fallback_date``). Prowenansja siedzi w tagach (``seed``, rodzaj, ``src:...``),
    nie w ``NoteMetadata`` — kontrakt notatki zostaje zamrożony.
    """
    heading = _HEADING_RE.search(text)
    title = heading.group(1).strip() if heading else _humanize(Path(source).stem)
    date_match = _DATE_RE.search(text)
    on = date.fromisoformat(date_match.group(1)) if date_match else fallback_date
    tags = ["seed", _kind_tag(source), f"src:{source}"]
    metadata = NoteMetadata(title=title, project=project, date=on, tags=tags)
    return metadata, text.strip()


def apply_seed(
    documents: Sequence[tuple[str, str]],
    *,
    writer: NotesWriter,
    service: NotesWriteService,
    company: str,
    project: str,
    fallback_date: date,
    write: bool,
) -> list[SeedResult]:
    """Zaplanuj (i przy ``write`` wykonaj) import; nigdy nie nadpisuje istniejącej notatki.

    Dla każdego dokumentu liczy DOKŁADNIE ten id, który policzyłby ``save_note`` (spójny
    ``build_note_id``), i sprawdza ``exists`` — istnieje → pominięcie (dedup); nie istnieje →
    ``utworzyłbym`` (dry-run) albo realny ``save_note`` (``write``). Błąd zapisu pojedynczego
    dokumentu nie kładzie całej partii — ląduje jako ``błąd`` i idzie dalej.

    Kolizję WEWNĄTRZ partii (dwa dokumenty → ten sam id) raportujemy osobno, śledząc id już
    zaplanowane w tym przebiegu — inaczej dry-run (nic nie zapisuje, więc ``exists`` cały czas
    ``False``) obiecałby N notatek, a ``--write`` utworzyłby N-1 (drugi wpadłby na ``exists``).
    Dzięki ``seen`` podgląd jest wierny zapisowi w obu trybach.
    """
    results: list[SeedResult] = []
    seen: set[str] = set()
    for source, text in documents:
        try:
            metadata, body = derive_note(source, text, project=project, fallback_date=fallback_date)
            target_id = build_note_id(company, project, metadata.date, metadata.title)
        except (ValueError, WriteError) as exc:
            results.append(SeedResult(source, "", ACTION_ERROR, str(exc)))
            continue
        if target_id in seen:
            results.append(SeedResult(source, target_id, ACTION_COLLISION))
            continue
        seen.add(target_id)
        if writer.exists(target_id):
            results.append(SeedResult(source, target_id, ACTION_SKIP))
            continue
        if not write:
            results.append(SeedResult(source, target_id, ACTION_WOULD_CREATE))
            continue
        try:
            note = service.save_note(metadata, body)
            results.append(SeedResult(source, note.id, ACTION_CREATED))
        except WriteError as exc:
            results.append(SeedResult(source, target_id, ACTION_ERROR, str(exc)))
    return results


def format_report(results: Sequence[SeedResult], *, write: bool) -> str:
    """Raport tekstowy: nagłówek trybu, linia na dokument, podsumowanie zliczeń."""
    mode = "ZAPIS" if write else "DRY-RUN (nic nie zapisano)"
    lines = [f"Seed korpusu — tryb {mode}:"]
    if not results:
        lines.append("Brak dokumentów do zaimportowania.")
        return "\n".join(lines)
    for r in results:
        suffix = f" — {r.detail}" if r.detail else ""
        target = r.note_id or "(bez id)"
        lines.append(f"• {r.source} → {target} · {r.action}{suffix}")
    counts = {
        "utworzono": sum(r.action == ACTION_CREATED for r in results),
        "do utworzenia": sum(r.action == ACTION_WOULD_CREATE for r in results),
        "pominięto": sum(r.action == ACTION_SKIP for r in results),
        "kolizje": sum(r.action == ACTION_COLLISION for r in results),
        "błędy": sum(r.action == ACTION_ERROR for r in results),
    }
    lines.append("Podsumowanie: " + " · ".join(f"{name} {value}" for name, value in counts.items()))
    return "\n".join(lines)


def _load_documents(
    source_dir: Path, pattern: str, *, recursive: bool
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Wczytaj obsługiwane dokumenty jako (ścieżka względna POSIX, tekst); stabilnie posortowane.

    Zwraca ``(dokumenty, pominięte)``. Rozszerzenie decyduje o ekstrakcji: md/txt/… czytamy jako
    tekst, docx/xlsx/pptx/pdf ekstrahujemy do tekstu (``document_text``). Plik o nieznanym
    rozszerzeniu ALBO nieczytelny (uszkodzony/zaszyfrowany) trafia do ``pominięte`` z powodem —
    NIE wywraca partii ani nie udaje pustej notatki. ``pominięte`` niesie (ścieżka, powód).
    """
    matches = source_dir.rglob(pattern) if recursive else source_dir.glob(pattern)
    files = sorted(p for p in matches if p.is_file())
    documents: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    for path in files:
        rel = path.relative_to(source_dir).as_posix()
        ext = path.suffix.lstrip(".").lower()
        if ext not in SUPPORTED_EXTS:
            skipped.append((rel, f"nieobsługiwane rozszerzenie .{ext or '(brak)'}"))
            continue
        try:
            documents.append((rel, extract_text_from_path(path)))
        except DocumentExtractionError as exc:
            skipped.append((rel, str(exc)))
    return documents, skipped


def main(argv: list[str] | None = None) -> int:
    """Zaimportuj dokumenty do korpusu notatek; domyślnie dry-run. 1 przy błędzie/pustym wejściu."""
    parser = argparse.ArgumentParser(
        prog="workmate-seed-corpus",
        description="Seed korpusu notatek z lokalnych markdownów (W0). Domyślnie dry-run.",
    )
    parser.add_argument("--source", required=True, help="katalog z dokumentami (markdown)")
    parser.add_argument("--project", required=True, help="klucz projektu z rejestru (cel importu)")
    parser.add_argument(
        "--date",
        default=_DEFAULT_FALLBACK_DATE.isoformat(),
        help="data dla dokumentów bez nagłówka Date: (RRRR-MM-DD, domyślnie stała)",
    )
    parser.add_argument(
        "--glob",
        default="*",
        help="wzorzec plików (domyślnie * — filtrowane po obsługiwanych rozszerzeniach)",
    )
    parser.add_argument("--recursive", action="store_true", help="przeszukaj też podkatalogi")
    parser.add_argument(
        "--write",
        action="store_true",
        help="faktycznie zapisz (bez tej flagi tylko pokazuje, co powstałoby)",
    )
    args = parser.parse_args(argv)

    # Raport drukuje tytuły z dokumentów (H1) — mogą nieść znaki spoza kodowania konsoli
    # (na Windows cp1250). UTF-8 z errors=replace, by druk nigdy nie wywrócił importu.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    try:
        fallback_date = date.fromisoformat(args.date)
    except ValueError:
        print(f"Zła data --date (oczekiwano RRRR-MM-DD): {args.date}")
        return 1

    source_dir = Path(args.source).expanduser()
    if not source_dir.is_dir():
        print(f"Nie znaleziono katalogu źródłowego: {source_dir}")
        return 1

    settings = Settings.from_env()
    # Import lokalny: rejestr i writer żyją w adapterach; trzymamy je poza rdzeniem.
    from workmate.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
    from workmate.adapters.outbound.yaml_projects_repo import YamlProjectsRepository

    projects = YamlProjectsRepository(settings.projects_registry)
    proj = projects.get(args.project)
    if proj is None:
        print(f"Projekt nie istnieje w rejestrze: {args.project!r}")
        return 1

    documents, skipped = _load_documents(source_dir, args.glob, recursive=args.recursive)
    if skipped:
        print("Pominięte pliki (nie zaimportowano):")
        for rel, reason in skipped:
            print(f"• {rel} — {reason}")
    writer = MarkdownNotesWriter(settings.notes_dir)
    service = NotesWriteService(writer, projects)
    results = apply_seed(
        documents,
        writer=writer,
        service=service,
        company=proj.company,
        project=proj.key,
        fallback_date=fallback_date,
        write=args.write,
    )
    print(format_report(results, write=args.write))
    return 1 if any(r.action == ACTION_ERROR for r in results) else 0
