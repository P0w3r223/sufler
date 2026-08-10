"""Renderowanie treści do pliku jako komenda powłoki:
``workmate-render --format pdf --output outputs/raport.pdf``.

Druga strona etapu 7 (ADR 0011 paczki): po zdjęciu narzędzia ``reply_with_file`` agent dostarcza
pliki, pisząc do skrzynki ``outputs/`` w katalogu roboczym rozmowy. ``md``/``txt`` zapisze wprost
powłoką (``> outputs/raport.md``), ale ``pdf``/``docx`` wymagają renderera (osadzony font Unicode,
``fpdf2``/``python-docx``) — tej komendy. Opis narzędzia ``Bash`` kieruje tu przy tych formatach,
więc bez niej obietnica formatów byłaby zdolnością milczącą: biblioteka leży w obrazie, a model
odpowiada „nie umiem zrobić PDF".

Treść bierze ze STDIN (potok z pliku roboczego albo heredoc), format i cel z flag. Rozdzielenie
treści (DANE) od polecenia jest celowe — treść nigdy nie trafia jako argument, więc nie ma jak
zostać zinterpretowana. Ten sam ``DefaultDocumentRenderer`` i ta sama biała lista
``FILE_REPLY_FORMATS`` co dawne ``reply_with_file`` — jedno źródło formatu i renderowania.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from workmate.adapters.inbound import env
from workmate.adapters.outbound.document_renderer import DefaultDocumentRenderer
from workmate.core.ports.document import FILE_REPLY_FORMATS

if TYPE_CHECKING:
    from collections.abc import Sequence


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="workmate-render",
        description=(
            "Zrenderuj treść ze STDIN do pliku i zapisz pod --output. "
            "Przykład: workmate-render --format pdf --output outputs/raport.pdf < raport.md"
        ),
    )
    parser.add_argument(
        "--format",
        required=True,
        choices=sorted(FILE_REPLY_FORMATS),
        help="Format wyjścia (== rozszerzenie pliku).",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Ścieżka pliku wynikowego (zwykle outputs/…, żeby trafił do skrzynki nadawczej).",
    )
    return parser.parse_args(list(argv))


def main() -> None:
    """Zrenderuj STDIN → plik.

    Kod 2 dla błędu użycia (argparse: brak flagi, zły format), kod 1 dla awarii renderowania
    (brakująca extra/font) lub zapisu. Model czyta niezerowy kod jako „popraw polecenie" (ADR 0057),
    więc sukces potwierdzamy TREŚCIĄ (rozmiar + typ MIME), a nie samą ciszą.
    """
    env.force_utf8_io()
    args = _parse_args(sys.argv[1:])
    body = sys.stdin.read()

    try:
        rendered = DefaultDocumentRenderer().render(body, args.format)
    except (RuntimeError, ValueError) as exc:
        print(f"Renderowanie nie powiodło się: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    out = Path(args.output)
    try:
        if out.parent != Path():
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(rendered.content)
    except OSError as exc:
        print(f"Zapis do {args.output} nie powiódł się: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"Zapisano {len(rendered.content)} B do {args.output} ({rendered.content_type}).")
