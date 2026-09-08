"""Ergonomia skoroszytu: co widać po przewinięciu, jak szerokie są kolumny, co jest ukryte.

Te własności nie mają odpowiednika w specyfikacji z fazy 2 — powstały z oceny gotowych
plików bramki 2, gdzie arkusz `Firmy` ma 43 kolumny, a ścieżka raportu zostawia kilkanaście
z nich strukturalnie pustych. Każdy test pilnuje jednej decyzji, żeby zmiana układu kolumn
nie odebrała po cichu tego, co poprawiono.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from ceidg_tool.exporter import MAX_COLUMN_WIDTH, _freeze_cell, write_workbook
from ceidg_tool.normalizer import FREEZE_AFTER, SHEETS, columns, normalize
from ceidg_tool.records import RawRecord, RowContext
from ceidg_tool.reports import UNFILLED_COLUMNS, row_to_record
from tests.test_exporter import META, make_records, source_of

# 24 kolumny dziennego raportu CSV (docs/decisions.md, wyniki mini-sondy)
REPORT_CSV_COLUMNS: tuple[str, ...] = (
    "Lp.",
    "Nip",
    "Regon",
    "NazwaPodmiotu",
    "Nazwisko",
    "Imie",
    "Telefon",
    "Email",
    "AdresWWW",
    "KodPocztowy",
    "Powiat",
    "Gmina",
    "Miejscowosc",
    "Ulica",
    "NrBudynku",
    "NrLokalu",
    "GlownyKodPkd",
    "PozostaleKodyPkd",
    "RokPKD",
    "StatusDzialalnosci",
    "DataRozpoczeciaDzialalnosci",
    "DataZakonczeniaDzialalnosci",
    "DataZawieszeniaDzialalnosci",
    "DataWznowieniaDzialalnosci",
)

# ----------------------------------------------------------------------------- zamrożenie


@pytest.mark.parametrize("sheet", sorted(FREEZE_AFTER))
def test_every_frozen_column_exists_in_its_sheet(sheet: str) -> None:
    """Literówka w `FREEZE_AFTER` cofnęłaby arkusz do samego nagłówka — i nikt by nie zauważył."""
    assert FREEZE_AFTER[sheet] in columns(sheet)


@pytest.mark.parametrize("sheet", sorted(FREEZE_AFTER))
def test_freeze_keeps_the_identity_columns_on_screen(sheet: str) -> None:
    names = list(columns(sheet))
    expected = get_column_letter(names.index(FREEZE_AFTER[sheet]) + 2)
    assert _freeze_cell(sheet, SHEETS[sheet]) == f"{expected}2"


def test_unknown_frozen_column_falls_back_to_the_header_row() -> None:
    """Zła nazwa nie może kosztować gotowego pobrania — eksport ma się zapisać mimo niej."""
    assert _freeze_cell("Nieistniejacy", SHEETS["Firmy"]) == "A2"


def test_firmy_leads_with_the_columns_a_human_reads(tmp_path: Path) -> None:
    """`nip` i `nazwa` przed resztą; `id` (GUID) zeszło do bloku technicznego przy `link`."""
    names = list(columns("Firmy"))
    assert names[:2] == ["nip", "nazwa"]
    assert names.index("id") > names.index("telefon")
    assert abs(names.index("id") - names.index("link")) == 1


# ----------------------------------------------------------------------------- szerokości


def test_capped_columns_are_narrow_but_keep_the_whole_value(tmp_path: Path) -> None:
    """Limit dotyczy wyświetlania. Skrócona kolumna nie może skrócić danych."""
    (dest,) = write_workbook(tmp_path / "out.xlsx", source_of(make_records()), metadata=META)
    ws = load_workbook(dest)["Firmy"]
    header = {c.value: i + 1 for i, c in enumerate(ws[1])}

    for name, cap in (("id", 18), ("link_ceidg", 24)):
        letter = get_column_letter(header[name])
        assert ws.column_dimensions[letter].width <= cap, name

    guid = ws.cell(row=2, column=header["id"]).value
    link = ws.cell(row=2, column=header["link_ceidg"]).value
    assert len(str(guid)) == 36  # pełny GUID, mimo kolumny szerokiej na 18 znaków
    assert str(link).endswith(str(guid).lower())


def test_uncapped_columns_still_size_themselves(tmp_path: Path) -> None:
    """Bez limitu obowiązuje stara reguła — inaczej limit stałby się szerokością domyślną."""
    (dest,) = write_workbook(tmp_path / "out.xlsx", source_of(make_records()), metadata=META)
    ws = load_workbook(dest)["Firmy"]
    header = {c.value: i + 1 for i, c in enumerate(ws[1])}
    widths = {
        name: ws.column_dimensions[get_column_letter(idx)].width for name, idx in header.items()
    }
    assert widths["nazwa"] <= 45  # max_width
    assert all(w <= MAX_COLUMN_WIDTH for w in widths.values())
    assert widths["email"] > widths["kraj"]  # dłuższa treść = szersza kolumna


# ----------------------------------------------------------------------------- ukrywanie


def test_hidden_columns_disappear_from_view_but_not_from_the_schema(tmp_path: Path) -> None:
    (dest,) = write_workbook(
        tmp_path / "out.xlsx",
        source_of(make_records()),
        metadata=META,
        hidden_columns={"link", "obywatelstwa"},
    )
    ws = load_workbook(dest)["Firmy"]
    header = {c.value: i + 1 for i, c in enumerate(ws[1])}
    assert list(header) == list(columns("Firmy"))  # automat czytający po nazwie nic nie traci

    def hidden(name: str) -> bool:
        return bool(ws.column_dimensions[get_column_letter(header[name])].hidden)

    assert hidden("link") and hidden("obywatelstwa")
    assert not hidden("nip") and not hidden("nazwa") and not hidden("telefon")


def test_nothing_is_hidden_by_default(tmp_path: Path) -> None:
    (dest,) = write_workbook(tmp_path / "out.xlsx", source_of(make_records()), metadata=META)
    ws = load_workbook(dest)["Firmy"]
    assert not any(dim.hidden for dim in ws.column_dimensions.values())


def test_report_unfilled_columns_all_exist_in_the_schema() -> None:
    """Literówka w `UNFILLED_COLUMNS` nie ukryłaby niczego i nie zgłosiłaby błędu.

    Nazwa musi występować w którymkolwiek arkuszu — `pkd_nazwa` żyje w `PKD`, nie w `Firmy`."""
    known = {name for sheet in SHEETS for name in columns(sheet)}
    assert UNFILLED_COLUMNS <= known


def test_unfilled_set_matches_what_row_to_record_actually_maps() -> None:
    """Zbiór wyprowadzony z mapowania, nie wypisany ręcznie — bo groźny jest dryf w drugą stronę.

    Lista pisana z pamięci wychwyci tylko kolumnę, o której ktoś pamiętał. Gdyby ktoś dopisał
    mapowanie do `row_to_record` (np. `terc`), a nazwa została w `UNFILLED_COLUMNS`, kolumna
    miałaby wartości i **nadal byłaby chowana** — dane znikłyby z widoku bez błędu, bez pustego
    miejsca w arkuszu i bez czerwonego testu. Ta asercja pyta wprost: co po przejściu pełnego
    wiersza raportu przez mapowanie zostaje puste?"""
    row = dict.fromkeys(REPORT_CSV_COLUMNS, "X") | {
        "Nip": "0123456789",
        "Regon": "012345678",
        "KodPocztowy": "01-234",
        "GlownyKodPkd": "6201Z",
        "PozostaleKodyPkd": "6202Z",
        "RokPKD": "2007",
        "StatusDzialalnosci": "Aktywny",
        "DataRozpoczeciaDzialalnosci": "2014-01-01",
        "DataZakonczeniaDzialalnosci": "2015-01-01",
        "DataZawieszeniaDzialalnosci": "2016-01-01",
        "DataWznowieniaDzialalnosci": "2017-01-01",
    }
    raw = RawRecord(
        id="x",
        list_json=row_to_record(row, wojewodztwo="podlaskie"),
        detail_json=None,
        list_utc="2026-09-05T09:00:00Z",
        detail_utc=None,
        detail_state="brak",
        zrodlo="CEIDG_RAPORT",
    )
    firmy = normalize(raw, RowContext(srodowisko="test", pobrano_utc="2026-09-05T10:00:00Z")).firmy

    empty = {name for name, value in firmy.items() if value is None}
    assert empty == UNFILLED_COLUMNS & set(columns("Firmy"))


def test_the_frozen_area_stays_usable_in_every_sheet(tmp_path: Path) -> None:
    """Zamrożenie obszaru szerszego niż ekran jest gorsze niż brak zamrożenia.

    Regresja z przeglądu: limit szerokości dostała tylko `Firmy.id`, a w arkuszach podrzędnych
    `id` stoi w kolumnie A — po zmianie `freeze_panes` na `C2` 38-znakowy GUID został tam
    przypięty do ekranu na stałe, czyli dokładnie ta wada, którą zamrożenie miało usunąć.
    Przed zmianą dało się go wyprowadzić przewijaniem; po niej już nie."""
    records = make_records()
    (dest,) = write_workbook(tmp_path / "out.xlsx", source_of(records), metadata=META)
    wb = load_workbook(dest)

    for sheet in (name for name in wb.sheetnames if name in FREEZE_AFTER):
        ws = wb[sheet]
        frozen = ws.freeze_panes
        assert frozen is not None and frozen != "A2", sheet
        last = ws[frozen].column - 1  # kolumny na lewo od komórki zamrożenia
        widths = [ws.column_dimensions[get_column_letter(i)].width for i in range(1, last + 1)]
        assert sum(widths) <= 75, f"{sheet}: zamrożone {sum(widths):.0f} znaków"


def test_a_width_cap_below_the_floor_is_refused() -> None:
    """`max_width=0` przeszłoby przez `or` jako „bez limitu", a wartość poniżej dolnej
    granicy zbiłaby kolumnę do nieczytelnej — oba po cichu, więc oba są błędem."""
    from ceidg_tool.normalizer import MIN_COLUMN_WIDTH, FieldSpec

    FieldSpec("ok", "x", "text", "opis", max_width=MIN_COLUMN_WIDTH)  # granica jest dozwolona
    for zle in (0, MIN_COLUMN_WIDTH - 1):
        with pytest.raises(ValueError, match="max_width"):
            FieldSpec("zla", "x", "text", "opis", max_width=zle)
