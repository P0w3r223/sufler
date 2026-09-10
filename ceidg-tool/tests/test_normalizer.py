from __future__ import annotations

import re
from datetime import date

from ceidg_tool.normalizer import (
    SHEETS,
    columns,
    get_path,
    merge_sources,
    normalize,
    slownik_rows,
)
from ceidg_tool.records import RawRecord, RowContext
from tests.conftest import detail_record, list_record

CTX = RowContext(srodowisko="test", pobrano_utc="2026-09-05T10:00:00Z")


def raw(idx: int = 1, *, with_detail: bool = True, **detail_overrides: object) -> RawRecord:
    return RawRecord(
        id=list_record(idx)["id"],
        list_json=list_record(idx),
        detail_json=detail_record(idx, **detail_overrides) if with_detail else None,
        list_utc="2026-09-05T09:00:00Z",
        detail_utc="2026-09-05T09:30:00Z" if with_detail else None,
        detail_state="pobrany" if with_detail else "brak",
        zrodlo="CEIDG_API",
    )


def test_headers_are_ascii_snake_case_and_unique() -> None:
    for sheet, cols in ((s, columns(s)) for s in SHEETS):
        assert len(set(cols)) == len(cols), sheet
        for col in cols:
            assert re.fullmatch(r"[a-z][a-z0-9_]*", col), (sheet, col)


def test_slownik_covers_every_column() -> None:
    described = {(sheet, col) for sheet, col, _ in slownik_rows()}
    for sheet in SHEETS:
        for col in columns(sheet):
            assert (sheet, col) in described


def test_record_with_four_pkd_gives_four_rows_and_joined_codes() -> None:
    rec = normalize(raw(), CTX)
    assert rec.firmy["pkd_glowny_kod"] == "3031Z"
    assert rec.firmy["pkd_wszystkie"] == "3031Z;6210B;0111Z;4711Z"
    assert rec.firmy["liczba_pkd"] == 4
    assert [r["pkd_kod"] for r in rec.pkd] == ["3031Z", "6210B", "0111Z", "4711Z"]
    assert [r["kolejnosc"] for r in rec.pkd] == [1, 2, 3, 4]
    assert [r["czy_glowny"] for r in rec.pkd] == [True, False, False, False]
    assert all(r["id"] == rec.firmy["id"] for r in rec.pkd)
    assert all(r["nip"] == "3563457932" for r in rec.pkd)


def test_detail_overrides_list_and_flags_details_present() -> None:
    rec = normalize(raw(), CTX)
    assert rec.firmy["nazwa"] == "Adam IntegracjaMGMF - Zmiana"
    assert rec.firmy["status"] == "WYLACZNIE_W_FORMIE_SPOLKI"
    assert rec.firmy["dane_szczegolowe"] is True
    assert rec.firmy["email"] == "adam@example.test"
    assert rec.firmy["pobrano_utc"] == "2026-09-05T09:30:00Z"
    assert rec.firmy["srodowisko"] == "test"
    assert rec.firmy["zrodlo"] == "CEIDG_API"


def test_record_without_details_is_legal() -> None:
    rec = normalize(raw(with_detail=False), CTX)
    assert rec.firmy["dane_szczegolowe"] is False
    assert rec.firmy["pkd_glowny_kod"] is None
    assert rec.firmy["nazwa"] == "Adam IntegracjaMGMF"
    assert rec.firmy["pobrano_utc"] == "2026-09-05T09:00:00Z"
    assert rec.pkd == [] and rec.spolki == [] and rec.adresy == []


def test_dates_become_date_objects_and_ids_stay_text() -> None:
    rec = normalize(raw(), CTX)
    assert rec.firmy["data_rozpoczecia"] == date(2014, 7, 29)
    assert rec.firmy["nip"] == "3563457932"
    assert rec.firmy["kod_pocztowy"] == "15-333"
    assert rec.firmy["terc"] == "2061011"
    assert rec.firmy["simc"] == "0922410"


def test_unparseable_date_is_kept_as_text() -> None:
    rec = normalize(raw(dataZawieszenia="brak"), CTX)
    assert rec.firmy["data_zawieszenia"] == "brak"
    rec2 = normalize(raw(dataZawieszenia="2020-01-05T00:00:00"), CTX)
    assert rec2.firmy["data_zawieszenia"] == date(2020, 1, 5)


def test_spolki_alias_and_sheet_rows() -> None:
    rec = normalize(raw(), CTX)
    assert rec.firmy["liczba_spolek"] == 1
    assert rec.spolki[0]["spolka_nip"] == "8567773578"
    assert rec.spolki[0]["spolka_regon"] == "113110043"

    aliased = normalize(
        raw(spolki=None, spolka={"nip": "1", "regon": "2", "zawieszenia": "2021-02-03"}), CTX
    )
    assert aliased.spolki[0]["spolka_nip"] == "1"
    assert aliased.spolki[0]["spolka_data_zawieszenia"] == date(2021, 2, 3)


def test_pkd_symbol_alias_and_missing_pkd_glowny() -> None:
    rec = normalize(raw(pkd=[{"symbol": "6201Z", "nazwa": "x"}], pkdGlowny=None), CTX)
    assert rec.firmy["pkd_wszystkie"] == "6201Z"
    assert rec.firmy["pkd_glowny_kod"] is None
    assert rec.pkd[0]["czy_glowny"] is False


def test_adresy_dodatkowe_and_korespondencyjny() -> None:
    extra = [{"ulica": "Lipowa", "budynek": "5", "lokal": "2", "miasto": "Łomża", "kod": "18-400"}]
    rec = normalize(raw(adresyDzialalnosciDodatkowe=extra), CTX)
    assert rec.adresy[0]["miasto"] == "Łomża"
    assert rec.adresy[0]["kod_pocztowy"] == "18-400"
    assert rec.adresy[0]["kolejnosc"] == 1
    assert rec.firmy["adres_korespondencyjny"] == "ul. Zwierzyniecka 1, 15-333 Białystok"
    assert rec.firmy["obywatelstwa"] == "Polska"


def test_merge_keeps_list_value_when_detail_is_null() -> None:
    r = raw(nazwa=None)
    assert merge_sources(r)["nazwa"] == "Adam IntegracjaMGMF"


def test_get_path_handles_missing_levels() -> None:
    assert get_path({"a": {"b": 1}}, "a.b") == 1
    assert get_path({"a": None}, "a.b") is None
    assert get_path({"a": "x"}, "a.b") is None
