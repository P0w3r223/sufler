"""Wyszukiwarka notatek jako komenda powłoki: ``workmate-search "integracja scada"``.

Ten sam ranker, którego używa narzędzie agenta i komenda ``/szukaj`` (BM25 nad lematami PL,
ADR 0023, plus fuzja z rankerem gęstym, gdy jest dostępny) — wystawiony powłoce. Powodem jest
przejście bazy wiedzy na pliki montowane w kontenerze: bez tej komendy agent szukałby
w korpusie fleksyjnym dopasowaniem wzorca, gdzie „migracja" nie trafia „migracji".

Wyjście jest BUDŻETEM TOKENOWYM, nie wydrukiem. Wynik polecenia wraca do kontekstu modelu
i jedzie ponownie w każdej kolejnej turze (ADR 0057/0058), więc domyślny format daje jedną
zwięzłą pozycję na notatkę, a ``--limit`` jest mały. ``--paths`` schodzi do samych ścieżek
(wejście dla ``xargs``/``cat``), ``--json`` służy skryptom.

Każda pozycja niesie ŚCIEŻKĘ PLIKU, nie samo logiczne id: następnym krokiem po wyszukaniu
jest przeczytanie notatki, a ścieżka czyni z tego zwykłe ``cat`` bez zgadywania układu katalogów.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from workmate.adapters.inbound import env
from workmate.adapters.inbound.agent_wiring import build_notes_service
from workmate.config import Settings
from workmate.core.errors import WorkMateError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from workmate.core.domain.models import NoteSummary

# Domyślnie mało wyników: każdy z nich zajmuje miejsce w kontekście do końca rozmowy.
_DEFAULT_LIMIT = 10
# Sufit fragmentu w wierszu — snippet z repozytorium bywa akapitem, a tu ma być jedną linią.
_SNIPPET_CHARS = 160


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="workmate-search",
        description=(
            "Przeszukaj notatki ze spotkań (ranking BM25 nad lematami polskimi). "
            'Przykład: workmate-search --limit 5 "integracja scada"'
        ),
    )
    parser.add_argument("query", nargs="*", help="Szukana fraza (słowa łączone spacją).")
    parser.add_argument("--project", default=None, help="Zawęź do projektu (klucz z rejestru).")
    parser.add_argument("--participant", default=None, help="Zawęź do uczestnika (fragment).")
    parser.add_argument(
        "--limit",
        type=int,
        default=_DEFAULT_LIMIT,
        help=f"Ile wyników (domyślnie {_DEFAULT_LIMIT}).",
    )
    parser.add_argument(
        "--paths", action="store_true", help="Same ścieżki plików, po jednej w wierszu."
    )
    parser.add_argument("--json", action="store_true", help="Wynik jako JSON (dla skryptów).")
    return parser.parse_args(list(argv))


def note_path(notes_dir: Path, note_id: str) -> str:
    """Ścieżka pliku notatki — id jest ścieżką względną bez rozszerzenia (``firma/projekt/…``)."""
    return str(notes_dir / f"{note_id}.md")


def _one_line(text: str) -> str:
    """Spłaszcz fragment do jednej linii i przytnij — pozycja wyniku ma zajmować jeden wiersz."""
    flat = " ".join(text.split())
    return flat if len(flat) <= _SNIPPET_CHARS else flat[:_SNIPPET_CHARS].rstrip() + "…"


def format_results(results: Sequence[NoteSummary], *, query: str, notes_dir: Path) -> str:
    """Zwięzły format czytelny dla człowieka i dla modelu: nagłówek plus 3 wiersze na notatkę."""
    if not results:
        return f"Brak notatek pasujących do: {query}"
    lines = [f"Znaleziono {len(results)} dla: {query}", ""]
    for position, r in enumerate(results, start=1):
        lines.append(f"{position}. {r.date} · {r.title} [{r.project}]")
        lines.append(f"   {note_path(notes_dir, r.id)}")
        snippet = _one_line(r.snippet)
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


def format_paths(results: Sequence[NoteSummary], *, notes_dir: Path) -> str:
    """Same ścieżki — wejście dla kolejnego polecenia w potoku."""
    return "\n".join(note_path(notes_dir, r.id) for r in results)


def format_json(results: Sequence[NoteSummary], *, query: str, notes_dir: Path) -> str:
    """JSON z tymi samymi polami co narzędzie agenta, wzbogacony o ``path``."""
    payload = {
        "query": query,
        "count": len(results),
        "results": [
            {**r.model_dump(mode="json"), "path": note_path(notes_dir, r.id)} for r in results
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render(args: argparse.Namespace, results: Sequence[NoteSummary], *, notes_dir: Path) -> str:
    """Wybierz format wyjścia wg flag (czysta funkcja — I/O zostaje w ``main``)."""
    query = " ".join(args.query).strip()
    if args.json:
        return format_json(results, query=query, notes_dir=notes_dir)
    if args.paths:
        return format_paths(results, notes_dir=notes_dir)
    return format_results(results, query=query, notes_dir=notes_dir)


def main() -> None:
    """Wyszukaj i wypisz wynik.

    Brak trafień kończy się kodem 0, a nie 1 jak w ``grep``: model czyta kod wyjścia jako
    „polecenie zawiodło, popraw je" (ADR 0057), więc puste wyszukiwanie sygnalizujemy TREŚCIĄ.
    Kod 2 zostaje dla błędu użycia (argparse), kod 1 dla awarii odczytu bazy wiedzy.
    """
    env.force_utf8_io()
    env.load_dotenv()

    args = _parse_args(sys.argv[1:])
    query = " ".join(args.query).strip()
    if not query:
        print('Podaj frazę, np.: workmate-search "integracja scada"', file=sys.stderr)
        raise SystemExit(2)

    settings = Settings.from_env()
    try:
        results = build_notes_service(settings).search_notes(
            query,
            project=args.project,
            participant=args.participant,
            limit=args.limit,
        )
    except WorkMateError as exc:
        print(f"Wyszukiwanie nie powiodło się: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(render(args, results, notes_dir=settings.notes_dir))
