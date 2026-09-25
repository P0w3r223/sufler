"""Ekstrakcja tekstu z pliku jako komenda powłoki: ``sufler-extract raport.pdf``.

Powłoka w wykonawcy czyta pliki `cat`-em, a to działa dokładnie do momentu, w którym plik nie
jest tekstem: PDF, docx, xlsx, pptx i HTML wracają wtedy jako bajty albo znaczniki. Ta komenda
domyka tę lukę od strony powłoki (ADR 0064) — tekstową ekstrakcję zostawiamy `Bash`-owi zamiast
dokładać ją do typowanej powierzchni narzędzi, bo kryterium ADR 0061 mówi, że narzędzie typowane
istnieje tylko tam, gdzie powłoka NIE sięga. Tu sięga — brakowało jej wyłącznie ekstraktora.

Ten sam ``document_text`` co materializer drzwi i importer korpusu, więc dla formatów
EKSTRAHOWANYCH (docx/xlsx/pptx/html/tekst) komenda i załącznik dają identyczny tekst — rozjazd
byłby błędem trudnym do zauważenia. PDF jest wyjątkiem świadomym: drzwi oddają go modelowi
NATYWNIE (blok ``document``), a ta komenda wyciąga z niego warstwę tekstową, więc skan bez tej
warstwy wróci tu pusty, choć jako załącznik byłby czytelny — dlatego notka o pustym PDF mówi
o tym wprost, zamiast zostawiać model z „plik nie ma tekstu".

Treść pliku to DANE: komenda ją wypisuje, niczego nie wykonuje i niczego nie interpretuje.
"""

from __future__ import annotations

import argparse
import contextlib
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from sufler.adapters.inbound import env
from sufler.adapters.inbound.document_text import (
    SUPPORTED_EXTS,
    DocumentExtractionError,
    extract_text_from_path,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sufler-extract",
        description=(
            "Wypisz tekst z pliku (pdf, docx, xlsx, pptx, html oraz formaty tekstowe) na STDOUT. "
            "Przykład: sufler-extract umowa.pdf | head -50"
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
    # Zachowaj się jak zwykły filtr uniksowy: bez tego `sufler-extract plik | head` — wzorzec,
    # do którego kieruje sam opis narzędzia — kończył się `BrokenPipeError` na STDERR, mimo że
    # potok zadziałał. Model widzi wyłącznie kod wyjścia i strumienie, więc sukces udawał awarię.
    # `head` zamyka wejście po swoich N liniach; SIGPIPE jest wtedy normalnym końcem, nie błędem.
    sigpipe = getattr(signal, "SIGPIPE", None)  # brak SIGPIPE (Windows) → bez zmian
    if sigpipe is not None:
        with contextlib.suppress(ValueError):  # ustawienie poza głównym wątkiem
            signal.signal(sigpipe, signal.SIG_DFL)
    args = _parse_args(sys.argv[1:])
    path = Path(args.path)

    # Katalog rozpoznajemy PYTANIEM, nie z rodzaju wyjątku. Odczyt katalogu podnosi
    # ``IsADirectoryError`` tylko na POSIX; Windows mapuje ten sam błąd na ``PermissionError``,
    # więc na maszynie deweloperskiej rada „to katalog, nie plik" degradowała do ogólnego
    # „nie udało się odczytać" — a to jest dokładnie ta różnica, którą model ma zobaczyć,
    # żeby poprawić polecenie. Gałąź ``except`` zostaje jako domknięcie wyścigu (katalog
    # podstawiony między sprawdzeniem a odczytem).
    if path.is_dir():
        print(f"To katalog, nie plik: {args.path}", file=sys.stderr)
        raise SystemExit(1)

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
        powod = (
            " Jeśli to skan, prześlij go jako załącznik — wtedy zobaczysz sam dokument."
            if path.suffix.lower() == ".pdf"
            else ""
        )
        print(f"Plik {args.path} jest czytelny, ale nie zawiera tekstu.{powod}", file=sys.stderr)
        return
    print(text)
