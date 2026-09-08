"""Zapis skoroszytu Excel (oraz CSV / JSONL) ze znormalizowanych rekordów — strumieniowo.

Arkusze `Firmy`, `PKD`, `Spolki`, `Adresy` (dwa ostatnie tylko gdy mają wiersze),
`Slownik` (generowany z `FieldSpec`), `Metadane`. Każdy arkusz z danymi to tabela
Excela z autofiltrem i zamrożonym nagłówkiem; kolumny `text` są zapisywane jako tekst,
więc Excel nie kasuje wiodących zer w NIP, REGON, kodzie pocztowym, TERC i SIMC.

Zasady z UZUPELNIENIE_01 §B/§C:
- dane z rejestru są wrogie: komórka tekstowa zaczynająca się od `=`, `+`, `-`, `@`,
  tabulatora lub CR dostaje prefiks apostrofu, znaki sterujące poza tabulatorem i nową
  linią są usuwane (Excel i CSV; JSONL nie jest arkuszem i zachowuje dane surowe,
  poprawnie zaescapowane przez JSON);
- zapis strumieniowy (`write_only`), bez ładowania całości do pamięci; rekordy
  dostarcza fabryka iteratorów, bo każda część i każdy plik czyta je od nowa;
- powyżej limitu wierszy Excela plik jest dzielony na części o tej samej strukturze;
- przed zapisem sprawdzane jest wolne miejsce; plik powstaje jako tymczasowy
  i jest podmieniany atomowo po sukcesie.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import warnings
from collections.abc import Callable, Collection, Iterator, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date
from itertools import islice
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import IllegalCharacterError
from openpyxl.worksheet.filters import AutoFilter
from openpyxl.worksheet.table import Table, TableColumn, TableStyleInfo

from .errors import ExportError
from .normalizer import (
    DATA_SHEETS,
    FREEZE_AFTER,
    MIN_COLUMN_WIDTH,
    SHEET_ADRESY,
    SHEET_FIRMY,
    SHEET_PKD,
    SHEET_SPOLKI,
    SHEETS,
    FieldSpec,
    NormalizedRecord,
    slownik_rows,
)
from .progress import Events, NullEvents
from .safetext import sanitize_text

EXCEL_MAX_ROWS = 1_048_576
DEFAULT_ROW_LIMIT = EXCEL_MAX_ROWS - 1  # wiersze danych; nagłówek zajmuje pierwszy
SHEET_SLOWNIK = "Slownik"
SHEET_METADANE = "Metadane"
ALWAYS_PRESENT: frozenset[str] = frozenset({SHEET_FIRMY})
MAX_COLUMN_WIDTH = 60
WIDTH_SAMPLE_ROWS = 500
EXPORT_REPORT_EVERY = 250  # co ile firm odświeżać pasek zapisu
CSV_DELIMITER = ";"
DATE_FORMAT = "YYYY-MM-DD"
TEXT_FORMAT = "@"
BYTES_PER_ROW_ESTIMATE = 160
MIN_FREE_BYTES = 200 * 1024 * 1024
TABLE_STYLE = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True, showColumnStripes=False)

RecordSource = Callable[[], Iterator[NormalizedRecord]]


def _add_table(ws: Any, name: str, columns: Sequence[str], rows: int) -> None:
    """Tabela Excela w arkuszu write_only: kolumny i autofiltr trzeba podać jawnie."""
    last_col = get_column_letter(len(columns))
    ref = f"A1:{last_col}{rows + 1}"
    table = Table(displayName=name, ref=ref)
    table.tableColumns = [TableColumn(id=i, name=c) for i, c in enumerate(columns, start=1)]
    table.autoFilter = AutoFilter(ref=ref)
    table.tableStyleInfo = TABLE_STYLE
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # openpyxl ostrzega mimo podanych kolumn
        ws.add_table(table)


def _rows_of(rec: NormalizedRecord) -> dict[str, list[dict[str, Any]]]:
    return {
        SHEET_FIRMY: [rec.firmy],
        SHEET_PKD: rec.pkd,
        SHEET_SPOLKI: rec.spolki,
        SHEET_ADRESY: rec.adresy,
    }


# ----------------------------------------------------------------------------- planowanie


@dataclass(frozen=True)
class ExportPlan:
    """Podział na części: liczba firm w każdej części i łączne wiersze per arkusz."""

    parts: tuple[int, ...]
    rows: dict[str, int]

    @property
    def total_records(self) -> int:
        return sum(self.parts)

    @property
    def total_rows(self) -> int:
        return sum(self.rows.values())


def plan_export(
    source: RecordSource,
    *,
    row_limit: int = DEFAULT_ROW_LIMIT,
    observer: Callable[[NormalizedRecord], None] | None = None,
    events: Events | None = None,
) -> ExportPlan:
    """Pierwsze przejście: liczy wiersze i wyznacza granice części tak, by żaden arkusz
    w żadnej części nie przekroczył `row_limit`. `observer` pozwala policzyć statystyki
    w tym samym przejściu zamiast czytać bazę jeszcze raz."""
    parts: list[int] = []
    current = {name: 0 for name in DATA_SHEETS}
    totals = {name: 0 for name in DATA_SHEETS}
    in_part = 0
    counted = 0
    reporter = events or NullEvents()
    for rec in source():
        if observer is not None:
            observer(rec)
        counted += 1
        if counted % EXPORT_REPORT_EVERY == 0:
            # Pierwsze przejście liczy wiersze, więc sumy jeszcze nie znamy — `0` znaczy
            # „nieokreślona", a pasek pokazuje sam licznik. Bez tego eksport 287 tys. firm
            # zaczyna się od kilku minut ciszy (zmierzone: 453 firmy/s).
            reporter.on_export(counted, 0)
        sizes = {name: len(rows) for name, rows in _rows_of(rec).items()}
        if any(size > row_limit for size in sizes.values()):
            raise ExportError(
                f"Rekord {rec.firmy.get('id')} ma więcej wierszy powiązanych niż mieści arkusz."
            )
        if in_part and any(current[n] + sizes[n] > row_limit for n in DATA_SHEETS):
            parts.append(in_part)
            current = {name: 0 for name in DATA_SHEETS}
            in_part = 0
        for name in DATA_SHEETS:
            current[name] += sizes[name]
            totals[name] += sizes[name]
        in_part += 1
    parts.append(in_part)
    return ExportPlan(parts=tuple(parts), rows=totals)


def check_free_space(
    directory: Path, expected_rows: int, *, min_free_bytes: int = MIN_FREE_BYTES
) -> None:
    """Odmawia startu, gdy na dysku nie ma miejsca na plik i jego kopię tymczasową."""
    directory.mkdir(parents=True, exist_ok=True)
    needed = expected_rows * BYTES_PER_ROW_ESTIMATE * 2 + min_free_bytes
    free = shutil.disk_usage(directory).free
    if free < needed:
        raise ExportError(
            f"Za mało miejsca w {directory}: wolne {free / 1e6:.0f} MB, "
            f"potrzeba około {needed / 1e6:.0f} MB."
        )


def part_paths(dest: Path, count: int) -> list[Path]:
    if count <= 1:
        return [dest]
    return [dest.with_name(f"{dest.stem}_czesc{i:02d}{dest.suffix}") for i in range(1, count + 1)]


# ----------------------------------------------------------------------------- Excel


def _cell_value(value: Any, kind: str) -> Any:
    if value is None:
        return None
    if kind == "date":
        return value if isinstance(value, date) else sanitize_text(str(value))
    if kind == "text":
        return sanitize_text(str(value))
    return value


def _freeze_cell(sheet: str, specs: Sequence[FieldSpec]) -> str:
    """Komórka zamrożenia: nagłówek plus kolumny tożsamości z `FREEZE_AFTER`.

    Nieznana nazwa kolumny cofa się do samego nagłówka zamiast wysypać eksport — schematu
    pilnuje test, ale pomyłka w mapowaniu nie może kosztować gotowego pobrania."""
    after = FREEZE_AFTER.get(sheet)
    names = [s.name for s in specs]
    if after is None or after not in names:
        return "A2"
    return f"{get_column_letter(names.index(after) + 2)}2"


class _SheetWriter:
    """Jeden arkusz w trybie write_only: nagłówek, szerokości, wiersze, tabela na końcu."""

    def __init__(
        self,
        wb: Workbook,
        name: str,
        specs: tuple[FieldSpec, ...],
        *,
        hidden: Collection[str] = (),
    ) -> None:
        self.name = name
        self.specs = specs
        self.hidden = frozenset(hidden)
        self.ws = wb.create_sheet(name)
        self.count = 0
        self._buffer: list[dict[str, Any]] = []
        self._started = False

    @property
    def is_empty(self) -> bool:
        return self.count == 0 and not self._buffer

    def add(self, row: dict[str, Any]) -> None:
        if not self._started:
            self._buffer.append(row)
            if len(self._buffer) >= WIDTH_SAMPLE_ROWS:
                self._start()
            return
        self._emit(row)

    def _start(self) -> None:
        # szerokości, ukrycia i zamrożenie muszą trafić do pliku przed pierwszym wierszem
        for idx, spec in enumerate(self.specs, start=1):
            longest = max(
                [len(spec.name)] + [len(str(r.get(spec.name) or "")) for r in self._buffer]
            )
            cap = min(spec.max_width or MAX_COLUMN_WIDTH, MAX_COLUMN_WIDTH)
            dim = self.ws.column_dimensions[get_column_letter(idx)]
            dim.width = min(max(longest + 2, MIN_COLUMN_WIDTH), cap)
            if spec.name in self.hidden:
                # kolumna zostaje w schemacie (automaty czytają ją tak samo), znika tylko
                # z oczu — inaczej ścieżka raportu pokazuje kilkanaście pustych kolumn
                dim.hidden = True
        self.ws.freeze_panes = _freeze_cell(self.name, self.specs)
        header = []
        for spec in self.specs:
            cell = WriteOnlyCell(self.ws, value=spec.name)
            cell.font = Font(bold=True)
            header.append(cell)
        self.ws.append(header)
        self._started = True
        for row in self._buffer:
            self._emit(row)
        self._buffer = []

    def _emit(self, row: dict[str, Any]) -> None:
        cells = []
        for spec in self.specs:
            value = _cell_value(row.get(spec.name), spec.kind)
            cell = WriteOnlyCell(self.ws, value=value)
            if value is not None and spec.kind == "text":
                cell.number_format = TEXT_FORMAT
                cell.data_type = "s"
            elif isinstance(value, date):
                cell.number_format = DATE_FORMAT
            cells.append(cell)
        self.ws.append(cells)
        self.count += 1

    def finish(self) -> None:
        if not self._started:
            self._start()
        if self.count:
            _add_table(self.ws, f"tbl_{self.name}", [s.name for s in self.specs], self.count)


def _write_slownik(wb: Workbook) -> None:
    ws = wb.create_sheet(SHEET_SLOWNIK)
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 32
    ws.column_dimensions["C"].width = 80
    ws.freeze_panes = "A2"
    header = []
    for name in ("arkusz", "kolumna", "opis"):
        cell = WriteOnlyCell(ws, value=name)
        cell.font = Font(bold=True)
        header.append(cell)
    ws.append(header)
    rows = slownik_rows()
    for row in rows:
        ws.append(list(row))
    _add_table(ws, "tbl_Slownik", ["arkusz", "kolumna", "opis"], len(rows))


def _write_metadane(wb: Workbook, metadata: Sequence[tuple[str, Any]]) -> None:
    ws = wb.create_sheet(SHEET_METADANE)
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 100
    ws.freeze_panes = "A2"
    header = []
    for name in ("klucz", "wartosc"):
        cell = WriteOnlyCell(ws, value=name)
        cell.font = Font(bold=True)
        header.append(cell)
    ws.append(header)
    for key, value in metadata:
        key_cell = WriteOnlyCell(ws, value=sanitize_text(str(key)))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            val_cell = WriteOnlyCell(ws, value=value)
        else:
            val_cell = WriteOnlyCell(ws, value=sanitize_text(str(value)))
            val_cell.data_type = "s"
        ws.append([key_cell, val_cell])


def _atomic_save(dest: Path, save: Callable[[Path], None]) -> None:
    """Zapis do pliku tymczasowego obok celu i `os.replace` po sukcesie."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp")
    try:
        save(tmp)
        os.replace(tmp, dest)
    except (OSError, IllegalCharacterError) as exc:
        raise ExportError(f"Nie można zapisać {dest}: {exc}") from exc
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _write_part(
    dest: Path,
    records: Iterator[NormalizedRecord],
    metadata: Sequence[tuple[str, Any]],
    hidden_columns: Collection[str] = (),
    events: Events | None = None,
    *,
    written_before: int = 0,
    total: int = 0,
) -> int:
    wb = Workbook(write_only=True)
    reporter = events or NullEvents()
    writers = {
        name: _SheetWriter(wb, name, SHEETS[name], hidden=hidden_columns) for name in DATA_SHEETS
    }
    written = written_before
    for rec in records:
        for name, rows in _rows_of(rec).items():
            for row in rows:
                writers[name].add(row)
        written += 1
        if written % EXPORT_REPORT_EVERY == 0:
            reporter.on_export(written, total)
    for name, writer in writers.items():
        if writer.is_empty and name not in ALWAYS_PRESENT:
            wb.remove(writer.ws)
            continue
        writer.finish()
    _write_slownik(wb)
    _write_metadane(wb, metadata)
    _atomic_save(dest, wb.save)
    reporter.on_export(written, total)
    return written


def write_workbook(
    dest: Path,
    source: RecordSource,
    *,
    metadata: Sequence[tuple[str, Any]],
    row_limit: int = DEFAULT_ROW_LIMIT,
    min_free_bytes: int = MIN_FREE_BYTES,
    observer: Callable[[NormalizedRecord], None] | None = None,
    hidden_columns: Collection[str] = (),
    events: Events | None = None,
) -> list[Path]:
    """Zapisuje skoroszyt (albo kilka części) i zwraca listę utworzonych plików.

    `hidden_columns` chowa kolumny, których dane źródło nie umie wypełnić — schemat zostaje
    pełny, więc automat czytający po nazwie nagłówka niczego nie traci."""
    plan = plan_export(source, row_limit=row_limit, observer=observer, events=events)
    check_free_space(dest.parent, plan.total_rows, min_free_bytes=min_free_bytes)
    paths = part_paths(dest, len(plan.parts))
    offset = 0
    # Jeden strumień na wszystkie części, a nie `islice(source(), offset, …)` dla każdej.
    # Tamto przewijało źródło od początku, więc część N-ta czytała z SQLite i normalizowała
    # od nowa wszystkie rekordy części wcześniejszych — koszt rósł kwadratowo z liczbą części,
    # a próg podziału jest osiągalny (287 tys. firm z raportu × kilka PKD).
    stream = source()
    for index, (path, size) in enumerate(zip(paths, plan.parts, strict=True), start=1):
        part_meta = list(metadata)
        if len(paths) > 1:
            part_meta.append(("czesc", f"{index}/{len(paths)}"))
            part_meta.append(("firm_w_czesci", size))
        written = _write_part(
            path,
            islice(stream, size),
            part_meta,
            hidden_columns,
            events,
            written_before=offset,
            total=plan.total_records,
        )
        offset = written
    return paths


# ----------------------------------------------------------------------------- CSV / JSONL


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return sanitize_text(str(value))


def write_csv(dest_dir: Path, source: RecordSource, *, events: Events | None = None) -> list[Path]:
    """Jeden plik CSV na arkusz (UTF-8 z BOM, separator `;`), te same kolumny co w Excelu.

    Jedno przejście po źródle na **wszystkie** arkusze, nie jedno na każdy. Poprzednia wersja
    wołała `source()` w pętli po `DATA_SHEETS`, więc `--formaty csv` czytało bazę i normalizowało
    każdy rekord cztery razy; razem z `xlsx` i `jsonl` dawało to około siedmiu pełnych przebiegów
    po SQLite dla jednego eksportu.

    Pliki powstają obok celu jako tymczasowe i są przemianowywane dopiero wtedy, gdy **cały**
    zapis się powiódł. Każdy plik pojawia się atomowo; zbiór plików nie jest transakcją — awaria
    pomiędzy przemianowaniami może zostawić ich część, ale nigdy pliku uciętego w połowie.
    Arkusze opcjonalne (`Spolki`, `Adresy`) powstają tylko, gdy mają wiersze.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    reporter = events or NullEvents()
    targets = {name: dest_dir / f"{name.lower()}.csv" for name in DATA_SHEETS}
    temps = {name: path.with_name(f".{path.name}.tmp") for name, path in targets.items()}
    counts = dict.fromkeys(DATA_SHEETS, 0)
    written: list[Path] = []
    try:
        with ExitStack() as stack:
            writers = {}
            for name in DATA_SHEETS:
                handle = stack.enter_context(
                    temps[name].open("w", encoding="utf-8-sig", newline="")
                )
                writer = csv.writer(handle, delimiter=CSV_DELIMITER)
                writer.writerow([spec.name for spec in SHEETS[name]])
                writers[name] = writer
            records = 0
            for rec in source():
                for name, rows in _rows_of(rec).items():
                    specs = SHEETS[name]
                    for row in rows:
                        writers[name].writerow([_csv_value(row.get(s.name)) for s in specs])
                        counts[name] += 1
                records += 1
                if records % EXPORT_REPORT_EVERY == 0:
                    reporter.on_export(records, 0)
        for name in DATA_SHEETS:
            if counts[name] == 0 and name not in ALWAYS_PRESENT:
                temps[name].unlink(missing_ok=True)
                continue
            os.replace(temps[name], targets[name])
            written.append(targets[name])
    except OSError as exc:
        raise ExportError(f"Nie można zapisać plików CSV w {dest_dir}: {exc}") from exc
    finally:
        for tmp in temps.values():
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    return written


def write_jsonl(dest: Path, source: RecordSource) -> Path:
    """Jeden obiekt JSON na firmę z zagnieżdżonymi listami `pkd`, `spolki`, `adresy`."""

    def save(tmp: Path) -> None:
        with tmp.open("w", encoding="utf-8") as fh:
            for rec in source():
                payload = {
                    **rec.firmy,
                    "pkd": rec.pkd,
                    "spolki": rec.spolki,
                    "adresy": rec.adresy,
                }
                fh.write(json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n")

    _atomic_save(dest, save)
    return dest


def _json_default(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
