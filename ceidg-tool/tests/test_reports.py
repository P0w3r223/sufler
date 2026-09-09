from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path

import pytest

from ceidg_tool.errors import ExportError
from ceidg_tool.records import Report
from ceidg_tool.reports import (
    STATUSY_SPOZA_RAPORTU,
    iter_report_rows,
    matches_criteria,
    parse_report_name,
    pick_registered_report,
    record_id_for,
    report_covers,
    row_to_record,
    status_to_api,
    statusy_poza_raportem,
)
from tests.support import criteria

HEADER = [
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
]


def row(**over: str) -> dict[str, str]:
    base = {
        "Lp.": "1",
        "Nip": "3563457932",
        "Regon": "618155359",
        "NazwaPodmiotu": "Testomir Testowy FIRMA",
        "Nazwisko": "Testowy",
        "Imie": "Testomir",
        "Telefon": "",
        "Email": "t@example.test",
        "AdresWWW": "",
        "KodPocztowy": "15-333",
        "Powiat": "Białystok",
        "Gmina": "Białystok",
        "Miejscowosc": "Białystok",
        "Ulica": "ul. Testowa",
        "NrBudynku": "1",
        "NrLokalu": "",
        "GlownyKodPkd": "6220B",
        "PozostaleKodyPkd": "4619Z$##$4722Z$##$62.10.B",
        "RokPKD": "2025",
        "StatusDzialalnosci": "Aktywny",
        "DataRozpoczeciaDzialalnosci": "2014-07-29",
        "DataZakonczeniaDzialalnosci": "",
        "DataZawieszeniaDzialalnosci": "",
        "DataWznowieniaDzialalnosci": "",
    }
    base.update(over)
    return base


def test_the_row_helper_stays_aligned_with_the_report_header() -> None:
    """`HEADER` i `row()` to jeden wiersz CSV rozpisany na dwa miejsca — i tak są używane.

    Trzy moduły testowe sklejają wiersz przez `";".join(r.values())`, czyli po kolejności
    kluczy `dict`, a nagłówek biorą z `HEADER`. Wystarczy literówka w `row(Miejscowsc=…)`,
    żeby `base.update(over)` dopisało dwudziestą piątą kolumnę na końcu: nagłówek zostaje
    dwudziestoczterokolumnowy, `DictReader` wrzuca nadmiar pod klucz `None`, a `iter_report_rows`
    go odfiltrowuje. Test wtedy nadal przechodzi — tylko sprawdza coś innego, niż pisał autor.
    """
    keys = list(row())

    assert keys == HEADER
    assert len(set(keys)) == len(keys)


def test_status_mapping_covers_report_texts() -> None:
    assert status_to_api("Aktywny") == "AKTYWNY"
    assert status_to_api("Zawieszony") == "ZAWIESZONY"
    assert status_to_api("Wykreślony") == "WYKRESLONY"
    assert (
        status_to_api("Działalność prowadzona wyłącznie w formie spółki cywilnej")
        == "WYLACZNIE_W_FORMIE_SPOLKI"
    )
    assert status_to_api("Coś nowego") == "COS_NOWEGO"


def test_row_to_record_matches_api_shape() -> None:
    rec = row_to_record(row(), wojewodztwo="podlaskie")
    assert rec["id"] == "NIP:3563457932"
    assert rec["wlasciciel"]["nip"] == "3563457932"
    assert rec["adresDzialalnosci"]["wojewodztwo"] == "PODLASKIE"
    assert rec["adresDzialalnosci"]["kod"] == "15-333"
    assert rec["pkdGlowny"] == {"kod": "6220B"}
    assert [p["kod"] for p in rec["pkd"]] == ["6220B", "4619Z", "4722Z", "6210B"]
    assert rec["status"] == "AKTYWNY"
    assert rec["dataRozpoczecia"] == "2014-07-29"
    assert "telefon" not in rec and rec["email"] == "t@example.test"
    assert "lokal" not in rec["adresDzialalnosci"]


def test_record_id_falls_back_to_regon_or_hash() -> None:
    assert record_id_for(row(Nip="")) == "REGON:618155359"
    assert record_id_for(row(Nip="", Regon="")).startswith("HASH:")


def test_record_id_is_stable_for_the_same_row_and_differs_for_different_ones() -> None:
    """`id` jest kluczem głównym w bazie, więc kolizja skleja dwa wiersze raportu w jeden."""
    bez_identyfikatorow = {"Nip": "", "Regon": ""}
    a = row(**bez_identyfikatorow, NazwaPodmiotu="FIRMA BEZ NIP", Miejscowosc="Białystok")
    b = row(**bez_identyfikatorow, NazwaPodmiotu="FIRMA BEZ NIP", Miejscowosc="Białystok")
    c = row(**bez_identyfikatorow, NazwaPodmiotu="INNA BEZ NIP", Miejscowosc="Łomża")

    assert record_id_for(a) == record_id_for(b)  # wiersze identyczne co do znaku
    assert record_id_for(a) != record_id_for(c)  # różnica w jakiejkolwiek kolumnie różnicuje skrót
    # kolejność kolumn w wierszu nie zmienia skrótu (klucze są sortowane)
    assert record_id_for(dict(reversed(list(a.items())))) == record_id_for(a)


def test_rows_sharing_a_nip_share_the_record_id() -> None:
    """Dwa wpisy tego samego przedsiębiorcy dostają jedno `id` — w bazie zostanie ostatni."""
    first = row(NazwaPodmiotu="FIRMA PIERWSZA")
    second = row(NazwaPodmiotu="FIRMA DRUGA")
    assert record_id_for(first) == record_id_for(second) == "NIP:3563457932"


def test_blank_identifiers_are_dropped_from_the_record_not_stored_as_empty() -> None:
    rec = row_to_record(row(Nip="", Regon="", Telefon="", Email=""), wojewodztwo="podlaskie")
    assert rec["id"].startswith("HASH:")
    assert "nip" not in rec["wlasciciel"] and "regon" not in rec["wlasciciel"]
    assert "telefon" not in rec and "email" not in rec
    assert not matches_criteria(rec, criteria(nip="3563457932"))


def test_matches_criteria_filters_locally() -> None:
    rec = row_to_record(row(), wojewodztwo="podlaskie")
    assert matches_criteria(rec, criteria(wojewodztwo="podlaskie"))
    assert matches_criteria(rec, criteria(data_od=date(2014, 1, 1), data_do=date(2014, 12, 31)))
    assert not matches_criteria(rec, criteria(data_od=date(2015, 1, 1)))
    assert matches_criteria(rec, criteria(status="AKTYWNY"))
    assert not matches_criteria(rec, criteria(status="ZAWIESZONY"))
    assert matches_criteria(rec, criteria(pkd="62.10.B"))
    assert not matches_criteria(rec, criteria(pkd="0111Z"))
    assert matches_criteria(rec, criteria(miasto="białystok"))
    assert matches_criteria(rec, criteria(nazwa="testowy"))
    assert not matches_criteria(rec, criteria(nazwa="inna"))
    assert matches_criteria(rec, criteria(nip="3563457932"))
    assert not matches_criteria(rec, criteria(kod="00-001"))


def fryzjer_2007() -> dict[str, str]:
    """Wiersz raportu tak, jak wygląda firma jeszcze nieprzeniesiona na PKD 2025.

    Kody i rocznik są zgodne ze sobą: `9602Z` istnieje w PKD 2007 i **nie istnieje** w 2025,
    więc `RokPKD` mówi tu 2007. Wiersz deklarujący rocznik, którego jego kody nie potwierdzają,
    byłby atrapą zaprzeczającą rejestrowi — a taka wygląda jak dowód.
    """
    return row(
        NazwaPodmiotu="Salon Fryzjerski",
        GlownyKodPkd="9602Z",
        PozostaleKodyPkd="",
        RokPKD="2007",
    )


def test_a_2007_coded_record_is_reached_only_by_the_widened_criteria() -> None:
    """ADR-0012: ścieżka raportowa filtruje lokalnie, więc rocznik nie kosztuje żądań.

    Salon zapisany jako `9602Z` jest niewidoczny dla filtru `9621Z` — to jest te 83 %
    fryzjerów, których zapytanie z PKD 2025 nie sięga. Rozszerzenie ma go wpuszczać
    **tym samym** polem, którym wpuszcza je zapytanie do API.
    """
    rec = row_to_record(fryzjer_2007(), wojewodztwo="podlaskie")

    assert not matches_criteria(rec, criteria(pkd="9621Z"))
    assert matches_criteria(rec, criteria(pkd="9621Z", pkd_2007="9602Z"))


def test_the_widened_criteria_still_reject_a_record_from_another_industry() -> None:
    """Rozszerzenie poszerza o poprzedników, nie o cokolwiek — inaczej filtr przestaje filtrować."""
    rec = row_to_record(row(GlownyKodPkd="4711Z", PozostaleKodyPkd=""), wojewodztwo="podlaskie")

    assert not matches_criteria(rec, criteria(pkd="9621Z", pkd_2007="9602Z"))


def test_the_report_path_matches_the_same_set_of_codes_the_api_would_get() -> None:
    """„Raport zamiast żądań" ma być wyborem obojętnym dla wyniku, więc zbiory kodów są jedne."""
    c = criteria(pkd="9621Z", pkd_2007="9602Z")
    rec = row_to_record(fryzjer_2007(), wojewodztwo="podlaskie")

    kody_rekordu = {p["kod"] for p in rec["pkd"]}

    assert kody_rekordu & set(c.wszystkie_pkd())
    assert matches_criteria(rec, c)


def test_parse_and_pick_report() -> None:
    assert parse_report_name("Zarejestrowane działalności - województwo podlaskie") == (
        "Zarejestrowane działalności",
        "podlaskie",
    )
    assert parse_report_name("Zarejestrowane działalności - brak województwa") == (
        "Zarejestrowane działalności",
        None,
    )
    reports = [
        Report(
            "1",
            "Zarejestrowane działalności - województwo podlaskie",
            ".csv",
            "u1",
            "2026-09-03 06:40:59",
        ),
        Report(
            "2",
            "Zarejestrowane działalności - województwo podlaskie",
            ".csv",
            "u2",
            "2026-09-04 06:40:59",
        ),
        Report(
            "3",
            "Zarejestrowane działalności - województwo podlaskie",
            ".xml",
            "u3",
            "2026-09-05 06:40:59",
        ),
        Report("4", "Złożone wnioski - województwo podlaskie", ".csv", "u4", "2026-09-05 06:30:00"),
    ]
    picked = pick_registered_report(reports, "Podlaskie")
    assert picked is not None and picked.id == "2"
    assert pick_registered_report(reports, "mazowieckie") is None
    assert report_covers(criteria(wojewodztwo="podlaskie"))
    assert not report_covers(criteria(miasto="Łomża"))


def test_iter_report_rows_streams_csv_from_zip(tmp_path: Path) -> None:
    csv_text = ";".join(HEADER) + "\n" + ";".join(row().values()) + "\n"
    path = tmp_path / "r.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Zarejestrowane działalności.csv", "﻿" + csv_text)
    rows = list(iter_report_rows(path))
    assert len(rows) == 1 and rows[0]["Nip"] == "3563457932"

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("x.csv", "a;b\n1;2\n")
    with pytest.raises(ExportError, match="kolumn"):
        list(iter_report_rows(bad))
    with pytest.raises(ExportError, match="ZIP"):
        list(iter_report_rows(tmp_path / "nie-ma.zip"))


# --------------------------------------------------------- A10: czego dziennego zrzutu nie ma


def test_raport_odmawia_zapytaniu_o_wpisy_oczekujace_na_rozpoczecie() -> None:
    """Rdzeń A10, oparty na pomiarze, nie na prozie.

    Zmierzone 2026-09-09 na `probe_out/raport_sample.zip` (287 256 wierszy, wielkopolskie,
    zero żądań): archiwum niesie dokładnie trzy wartości `StatusDzialalnosci` — „Aktywny",
    „Zawieszony" i „Działalność prowadzona wyłącznie w formie spółki cywilnej". Wpisów
    oczekujących na rozpoczęcie nie ma tam ani jednego, a `report_covers` odmawiał wyłącznie
    przy `WYKRESLONY` — więc takie zapytanie szło ścieżką raportu i wracało puste, bez
    błędu i bez ostrzeżenia. Ścieżka raportu jest tańsza o cztery rzędy wielkości, więc
    `--zrodlo auto` wybierał ją domyślnie: cicha pustka była wariantem domyślnym.
    """
    pytanie = criteria(wojewodztwo="podlaskie", status="OCZEKUJE_NA_ROZPOCZECIE_DZIALANOSCI")

    assert not report_covers(pytanie)
    assert statusy_poza_raportem(pytanie) == ("OCZEKUJE_NA_ROZPOCZECIE_DZIALANOSCI",)


def test_raport_nadal_odmawia_zapytaniu_o_wykreslonych() -> None:
    """Kontrola regresji: starszy warunek nie mógł zniknąć przy dokładaniu drugiego."""
    assert not report_covers(criteria(wojewodztwo="podlaskie", status="WYKRESLONY"))


def test_raport_pokrywa_statusy_ktore_naprawde_w_nim_sa() -> None:
    """Kontrola pozytywna. Bez niej „odmawiaj zawsze" przeszłoby oba testy wyżej i zabrało
    ścieżkę, która oddaje 287 256 rekordów za jedno żądanie."""
    assert report_covers(criteria(wojewodztwo="podlaskie", status="AKTYWNY"))
    assert report_covers(criteria(wojewodztwo="podlaskie", status="ZAWIESZONY"))
    assert statusy_poza_raportem(criteria(wojewodztwo="podlaskie", status="AKTYWNY")) == ()


def test_ekran_oferty_wymienia_oba_brakujace_statusy() -> None:
    """Odmowa działa tylko wtedy, gdy operator wie, czego szukać gdzie indziej. Ekran
    wymieniał sam WYKREŚLONY, więc drugi brak był niewidoczny również dla człowieka."""
    from ceidg_tool.ui.texts import report_offer

    blok = report_offer(Report("1", "Raport", ".csv", "u", "2026-09-05 06:00:00"))
    brakuje = next(wiersz[1] for wiersz in blok.rows if wiersz[0] == "czego brakuje")

    assert "WYKREŚLONYCH" in brakuje
    assert "OCZEKUJĄCYCH NA ROZPOCZĘCIE" in brakuje


def test_ekran_nazywa_kazdy_status_ktory_lista_odrzuca() -> None:
    """Uwaga z przeglądu: `STATUSY_SPOZA_RAPORTU` decyduje, a `texts.STATUS_BRAK_W_RAPORCIE`
    nazywa — dwie stałe, bo `texts` musi zostać czyste (reguła 6) i nie może importować
    `reports`. Dopisanie trzeciego statusu tylko do jednej z nich byłoby A10 jeszcze raz:
    lista odmawiała dwóch przypadków, a ekran wymieniał jeden. Skoro obie stałe nie mogą się
    zobaczyć, zgodności pilnuje test — tak jak przy parze `WAIT_SLICE_S`/`DEFAULT_LOCK_STALE_S`."""
    from ceidg_tool.ui.texts import STATUS_BRAK_W_RAPORCIE, report_offer

    assert set(STATUS_BRAK_W_RAPORCIE) == set(STATUSY_SPOZA_RAPORTU)

    blok = report_offer(Report("1", "Raport", ".csv", "u", "2026-09-05 06:00:00"))
    brakuje = next(wiersz[1] for wiersz in blok.rows if wiersz[0] == "czego brakuje")
    for nazwa in STATUS_BRAK_W_RAPORCIE.values():
        assert nazwa in brakuje


# --------------------------------------------------- F12: parytet filtra miasta z serwerem


def test_filtr_miasta_dopasowuje_fragmentem_tak_jak_serwer() -> None:
    """Rdzeń F12, zmierzony na produkcji za zero żądań (audyt 2026-09-08, zamknięte 09-09).

    Run `eb1df3a8` z filtrem `miasto=['Łomża']` zwrócił z API cztery wpisy z miejscowości
    „Stara Łomża przy Szosie" i „Stara Łomża nad Rzeką" — nazw zawierających „Łomża", ale jej
    nierównych. Sprawdzone dodatkowo, że żaden z tych wpisów nie ma „Łomża" w adresie
    korespondencyjnym ani w żadnym innym, więc dopasowanie nie mogło pójść inną drogą.

    `matches_criteria` porównywał miasto dokładnie, więc ścieżka raportu **gubiła** te wpisy,
    a komentarz nad funkcją zapewniał, że oba źródła dają ten sam zbiór.
    """
    wiersz = row(Miejscowosc="Stara Łomża przy Szosie")
    rekord = row_to_record(wiersz, wojewodztwo="podlaskie")

    assert matches_criteria(rekord, criteria(miasto="Łomża"))


def test_filtr_miasta_nadal_odrzuca_miasto_bez_wspolnego_fragmentu() -> None:
    """Kontrola pozytywna. „Zawiera" zamienione na „cokolwiek" byłoby tym samym defektem
    obróconym o 180 stopni: ścieżka raportu zwracałaby nadzbiór zamiast podzbioru."""
    rekord = row_to_record(row(Miejscowosc="Białystok"), wojewodztwo="podlaskie")

    assert not matches_criteria(rekord, criteria(miasto="Łomża"))


def test_pola_niezmierzone_zostaja_przy_porownaniu_doklandym() -> None:
    """Granica poprawki, i to jest jej istota. Sonda z 2026-09-09 pokazała, że rodzina pól
    tekstowych **nie jest jednorodna**, więc rozciągnięcie „fragmentu" z `miasto` na sąsiednie
    pola byłoby zgadywaniem — tym samym, które trzymało `miasto` przy porównaniu dokładnym.
    Póki nie zmierzono, `powiat` porównuje się dokładnie i test to przypina."""
    rekord = row_to_record(row(Powiat="łomżyński"), wojewodztwo="podlaskie")

    assert matches_criteria(rekord, criteria(powiat="łomżyński"))
    assert not matches_criteria(rekord, criteria(powiat="omżyń"))
