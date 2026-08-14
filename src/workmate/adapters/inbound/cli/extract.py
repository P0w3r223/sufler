"""Ekstrakcja tekstu z pliku jako komenda powłoki: ``workmate-extract raport.pdf``.

Powłoka w wykonawcy czyta pliki `cat`-em, a to działa dokładnie do momentu, w którym plik nie
jest tekstem: PDF, docx, xlsx, pptx i HTML wracają wtedy jako bajty albo znaczniki. Ta komenda
domyka tę lukę od strony powłoki (ADR 0064) — tekstową ekstrakcję zostawiamy `Bash`-owi zamiast
dokładać ją do typowanej powierzchni narzędzi, bo kryterium ADR 0061 mówi, że narzędzie typowane
istnieje tylko tam, gdzie powłoka NIE sięga. Tu sięga — brakowało jej wyłącznie ekstraktora.

Ten sam ``document_text`` co materializer drzwi i importer korpusu, więc `workmate-extract` i
`File(read)` widzą ten sam tekst — rozjazd między nimi byłby błędem trudnym do zauważenia.
Treść pliku to DANE: komenda ją wypisuje, niczego nie wykonuje i niczego nie interpretuje.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from workmate.adapters.inbound import env
from workmate.adapters.inbound.document_text import (
    SUPPORTED_EXTS,
    DocumentExtractionError,
    extract_text_from_path,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="workmate-extract",
        description=(
            "Wypisz tekst z pliku (pdf, docx, xlsx, pptx, html oraz formaty tekstowe) na STDOUT. "
            "Przykład: workmate-extract umowa.pdf | head -50"
        ),
        epilog=(
            "Obsługiwane rozszerzenia: " + ", ".join(sorted(SUPPORTED_EXTS)) + ". "
            "Wyjście bywa długie — powłoka i tak przycina je do swojego limitu, więc filtruj "
            "(grep/head), zamiast wypisywać całość."
        ),
    )
    parser.add_argument("path", help="Ścieżka pliku do ekstrakcji.")
    return parser.parse_args(list(argv))


def main() -> None:
    """Wypisz tekst pliku na STDOUT.

    Kod 2 dla błędu użycia (argparse), kod 1 dla nieczytelnego pliku, nieobsługiwanego
    rozszerzenia albo braku pliku. Model czyta niezerowy kod jako „popraw polecenie" (ADR 0057),
    więc powód idzie na STDERR zdaniem, a nie samą ciszą — inaczej „pusto" i „nie umiem" wyglądają
    dla niego identycznie. Pusty wynik (np. skan PDF bez warstwy tekstowej) to SUKCES z jawną
    notką, bo plik jest czytelny — po prostu nie ma w nim tekstu.
    """
    env.force_utf8_io()
    args = _parse_args(sys.argv[1:])
    path = Path(args.path)

    try:
        text = extract_text_from_path(path)
    except FileNotFoundError:
        print(f"Nie ma pliku: {args.path}", file=sys.stderr)
        raise SystemExit(1) from None
    except IsADirectoryError:
        print(f"To katalog, nie plik: {args.path}", file=sys.stderr)
        raise SystemExit(1) from None
    except OSError as exc:
        print(f"Nie udało się odczytać {args.path}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except DocumentExtractionError as exc:
        print(f"Ekstrakcja nie powiodła się: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if not text:
        print(f"Plik {args.path} jest czytelny, ale nie zawiera tekstu.", file=sys.stderr)
        return
    print(text)
