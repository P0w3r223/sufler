"""Zapis arkusza importu do ``.xlsx`` (ADR 0035) — jedyne miejsce w repo, które PISZE Excela.

Import ``openpyxl`` jest LENIWY (extra ``worklogi``), jak reszta zależności opcjonalnych. Adapter
jest celowo głupi: dostaje gotowe nagłówki i wiersze z rdzenia i tylko je zrzuca. Cała wiedza
o formacie WorklogPRO — nazwy kolumn, notacja czasu, offset ISO — mieszka w
``core/domain/timesheet_sheet.py``, gdzie da się ją przetestować bez SDK.

Do tej pory ``openpyxl`` służył w repo wyłącznie do ODCZYTU załączników
(``teams_graph/attachments.py``); to pierwszy zapis.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MISSING = "Zapis arkuszy wymaga extra 'worklogi'. Zainstaluj: uv sync --extra worklogi"
# Nazwa arkusza w skoroszycie. WorklogPRO czyta PIERWSZY arkusz, ale czytelna nazwa pomaga
# człowiekowi, który otworzy plik przed importem.
_SHEET_TITLE = "Worklogi"


class OpenpyxlSheetWriter:
    """Zrzut ``Sheet`` (nagłówki + wiersze) do pliku ``.xlsx``."""

    def write(self, path: str, headers: tuple[str, ...], rows: tuple[tuple[Any, ...], ...]) -> None:
        """Zapisz arkusz pod ``path``, tworząc brakujące katalogi; nadpisz istniejący plik.

        Nadpisanie jest ZAMIERZONE: ścieżka jest deterministyczna (osoba + tydzień), więc
        powtórzony przebieg po awarii ma dać ten sam plik, a nie drugi obok. Wszystkie komórki
        piszemy jako TEKST — daty i czasy mają już poprawny format z rdzenia, a Excel potrafi
        „pomóc" i przekształcić ``2026-07-15T08:00:00.000+0200`` we własny typ daty, psując to,
        co WorklogPRO ma potem sparsować.
        """
        try:
            from openpyxl import Workbook
        except ImportError as exc:  # pragma: no cover - zależy od instalacji, nie od logiki
            raise SystemExit(_MISSING) from exc

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = _SHEET_TITLE
        sheet.append(list(headers))
        for row in rows:
            sheet.append(["" if cell is None else str(cell) for cell in row])
        _widen(sheet, headers, rows)
        workbook.save(target)
        logger.info("Arkusz zapisany: %s (%d wierszy)", target, len(rows))


def _widen(sheet: Any, headers: tuple[str, ...], rows: tuple[tuple[Any, ...], ...]) -> None:
    """Dopasuj szerokość kolumn do treści — plik ogląda człowiek, zanim go zaimportuje.

    Kosmetyka, ale tania: domyślne szerokości pokazują ``#####`` zamiast znacznika czasu,
    a wtedy nikt nie zweryfikuje, czy dane się zgadzają.
    """
    from openpyxl.utils import get_column_letter

    for index, header in enumerate(headers, start=1):
        longest = max(
            [len(header)] + [len(str(row[index - 1])) for row in rows if index <= len(row)]
        )
        sheet.column_dimensions[get_column_letter(index)].width = min(longest + 2, 60)
