#!/usr/bin/env python
"""Buduje `ceidg_tool/data/pkd<rocznik>.yaml` z **oficjalnego** pliku klasyfikacji.

Domyślny rocznik to **2025**, bo tyle zwraca rejestr: każde `rokPkd` w zmierzonych
odpowiedziach API mówi 2025 (`docs/decisions.md`). Kody obu roczników częściowo się różnią —
`4933Z` istnieje tylko w nowszym — więc słownik z niewłaściwego rocznika odrzucałby kody
poprawne albo podpowiadał nieistniejące.

Dlaczego skrypt, a nie plik wpisany ręcznie albo wygenerowany przez model: to kilkaset
podklas z rozporządzenia. Lista przepisana z pamięci albo
wyciągnięta ze streszczenia strony przeszłaby **wszystkie** automatyczne sprawdzenia z
ADR-0011 — kanoniczność kluczy, liczbę wpisów, zgodność z kodami z fixtures — i mogłaby być
przy tym cicho błędna w nazwach, których nikt nie porówna. Jedyne, co łapie błąd u źródła, to
źródło. Skrypt jest więc przepisywaniem mechanicznym, bez modelu w pętli, a prowenienacja
(plik wejściowy, data, suma kontrolna) ląduje w nagłówku wyniku.

Skąd wziąć wejście: `https://klasyfikacje.stat.gov.pl/Pkd2025` → pobranie schematu
klasyfikacji. Wyszukiwarka GUS jest aplikacją JS, więc pliku nie da się pobrać poleceniem —
robi to człowiek, raz.

Użycie:

    PYTHONUTF8=1 python scripts/build_pkd.py pkd2025.xlsx
    PYTHONUTF8=1 python scripts/build_pkd.py pkd.csv --kod Symbol --nazwa Nazwa
    PYTHONUTF8=1 python scripts/build_pkd.py stary.csv --rocznik 2007  # tylko archiwalnie

Skrypt bierze wyłącznie **podklasy** (kod w postaci `62.01.Z` albo `6201Z`); sekcje, działy,
grupy i klasy pomija, bo `Criteria.pkd` przyjmuje tylko podklasę.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ceidg_tool.criteria import normalize_pkd  # noqa: E402

DANE = Path(__file__).resolve().parent.parent / "ceidg_tool" / "data"
DEFAULT_ROCZNIK = "2025"

# Ile pierwszych wierszy przeszukać w poszukiwaniu nagłówka (arkusze GUS mają wiersz tytułowy).
NAGLOWEK_SZUKAJ_W = 10

# Nazwy kolumn spotykane w eksportach GUS i w publikacjach wtórnych. Kolejność ma znaczenie:
# wygrywa pierwsza obecna. Jawne `--kod`/`--nazwa` przebijają zgadywanie.
# „Podklasa" jest pierwsza, bo tak nazywa się kolumna w `StrukturaPKD2025.xls` z GUS — a to
# jedyna kolumna niosąca kody pięcioznakowe; „Klasa" obok niej ma czteroznakowe i wpadłaby
# w odrzucenie dopiero na `normalize_pkd`, ciszej, niż powinna.
KOD_KOLUMNY = ("Podklasa", "podklasa", "Symbol", "symbol", "Kod", "kod", "PKD", "pkd")
NAZWA_KOLUMNY = ("Nazwa", "nazwa", "Nazwa grupowania", "Opis", "opis")


def wykryj_kolumne(naglowek: list[str], kandydaci: tuple[str, ...], czego: str) -> str:
    for nazwa in kandydaci:
        if nazwa in naglowek:
            return nazwa
    raise SystemExit(
        f"Nie znalazłem kolumny z {czego}. Nagłówek pliku: {naglowek}. "
        f"Wskaż ją jawnie: --{czego} NAZWA_KOLUMNY"
    )


def wiersze_xlsx(path: Path) -> Iterator[dict[str, str]]:
    """Wiersze arkusza jako słowniki — pierwszy wiersz jest nagłówkiem.

    XLSX obsługujemy wprost, żeby nie zmuszać nikogo do przejścia przez Excela i „zapisz jako
    CSV". Ten objazd ma własny tryb awarii, i to dokładnie ten, którego szukają sprawdzenia
    słownika: Excel potrafi z `62.01.Z` zrobić datę, a z kodu z zerem wiodącym — liczbę.
    """
    from openpyxl import load_workbook

    arkusz = load_workbook(path, read_only=True, data_only=True).active
    if arkusz is None:
        raise SystemExit(f"Plik {path} nie ma aktywnego arkusza.")
    wiersze = arkusz.iter_rows(values_only=True)
    naglowek = _znajdz_naglowek(wiersze, path)
    for wiersz in wiersze:
        yield {
            naglowek[i]: ("" if wartosc is None else str(wartosc))
            for i, wartosc in enumerate(wiersz)
            if i < len(naglowek)
        }


def _znajdz_naglowek(wiersze: Iterator[tuple[Any, ...]], path: Path) -> list[str]:
    """Pierwszy wiersz zawierający znaną kolumnę z kodem — nie zawsze jest nim wiersz pierwszy.

    `StrukturaPKD2025.xls` z GUS zaczyna się od wiersza tytułowego („Polska Klasyfikacja
    Działalności 2025"), a nagłówek siedzi dopiero pod nim. Sztywne „pierwszy wiersz to nagłówek"
    dawało tu kolumny o nazwach pustych i zero podklas — czyli awarię wyglądającą jak zły plik.
    """
    for _ in range(NAGLOWEK_SZUKAJ_W):
        wiersz = next(wiersze, None)
        if wiersz is None:
            break
        komorki = [str(c).strip() if c is not None else "" for c in wiersz]
        if set(komorki) & set(KOD_KOLUMNY):
            return komorki
    raise SystemExit(
        f"W pierwszych {NAGLOWEK_SZUKAJ_W} wierszach {path.name} nie ma kolumny z kodem "
        f"(szukam: {', '.join(KOD_KOLUMNY)}). Wskaż ją jawnie: --kod NAZWA_KOLUMNY"
    )


def czytaj(path: Path, kod_kol: str | None, nazwa_kol: str | None) -> Iterator[tuple[str, str]]:
    """Zwraca pary (kod kanoniczny, nazwa) wyłącznie dla podklas. CSV albo XLSX."""
    if path.suffix.lower() == ".xls":
        # Prawdziwy BIFF8 (OLE2), a nie XLSX z inną końcówką — GUS udostępnia właśnie taki.
        # `openpyxl` go nie czyta, a dokładanie `xlrd` do zależności dla jednorazowej konwersji
        # byłoby nieproporcjonalne. LibreOffice robi to bezstratnie i bez heurystyk importu,
        # którymi Excel potrafi zamienić `01.11.Z` na datę.
        raise SystemExit(
            f"{path.name} jest w starym formacie .xls, którego nie czytam. Przekonwertuj:\n"
            '  "C:/Program Files/LibreOffice/program/soffice.exe" --headless '
            f"--convert-to xlsx --outdir {path.parent} {path}\n"
            "a potem podaj powstały plik .xlsx."
        )
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        wiersze = list(wiersze_xlsx(path))
        naglowek = list(wiersze[0]) if wiersze else []
        kod_kol = kod_kol or wykryj_kolumne(naglowek, KOD_KOLUMNY, "kod")
        nazwa_kol = nazwa_kol or wykryj_kolumne(naglowek, NAZWA_KOLUMNY, "nazwa")
        yield from _pary(wiersze, kod_kol, nazwa_kol)
        return
    with path.open(encoding="utf-8-sig", newline="") as fh:
        próbka = fh.read(4096)
        fh.seek(0)
        try:
            dialekt = csv.Sniffer().sniff(próbka, delimiters=";,\t")
        except csv.Error:
            dialekt = csv.excel  # type: ignore[assignment]
        reader = csv.DictReader(fh, dialect=dialekt)
        naglowek = list(reader.fieldnames or [])
        kod_kol = kod_kol or wykryj_kolumne(naglowek, KOD_KOLUMNY, "kod")
        nazwa_kol = nazwa_kol or wykryj_kolumne(naglowek, NAZWA_KOLUMNY, "nazwa")
        yield from _pary(reader, kod_kol, nazwa_kol)


def _pary(
    wiersze: Iterable[Mapping[str, str]], kod_kol: str, nazwa_kol: str
) -> Iterator[tuple[str, str]]:
    """Wspólne filtrowanie dla CSV i XLSX: same podklasy, nazwy o znormalizowanych odstępach."""
    for wiersz in wiersze:
        surowy = (wiersz.get(kod_kol) or "").strip()
        nazwa = " ".join((wiersz.get(nazwa_kol) or "").split())
        if not surowy or not nazwa:
            continue
        try:
            kod = normalize_pkd(surowy)
        except ValueError:
            continue  # sekcja, dział, grupa albo klasa — nie podklasa
        yield kod, nazwa


def zapisz(
    pary: dict[str, str], *, out: Path, zrodlo: Path, suma: str, rocznik: str, podstawa: str | None
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    # Podstawy prawnej **nie zgadujemy**. Dla PKD 2007 to Dz.U. 2007 nr 251 poz. 1885; dla
    # nowszego rocznika wpisuje ją ten, kto ma przed sobą dokument źródłowy (`--podstawa`).
    # Nagłówek ma opisywać zawartość, a nie brzmieć wiarygodnie.
    naglowek = [
        f"# Polska Klasyfikacja Działalności {rocznik} — podklasy (kod: nazwa).",
        "#",
        f"# Podstawa prawna: {podstawa or 'patrz dokument źródłowy (nie podano przy budowaniu)'}",
        f"# Wyszukiwarka GUS: https://klasyfikacje.stat.gov.pl/Pkd{rocznik}",
        "#",
        f"# Zbudowane: {datetime.now(tz=UTC).date().isoformat()} przez scripts/build_pkd.py",
        f"# Plik źródłowy: {zrodlo.name}",
        f"# SHA-256 źródła: {suma}",
        "#",
        "# Plik jest generowany. Nie edytuj go ręcznie — popraw źródło i zbuduj ponownie,",
        "# inaczej prowenienacja w tym nagłówku przestaje opisywać zawartość.",
        "",
    ]
    # Cytowanie zostawiamy bibliotece. Ręczne `"{kod}": "{nazwa}"` zakładało, że w nazwie nie
    # ma cudzysłowu ani odwrotnego ukośnika — nazwa z cudzysłowem dawała plik, którego
    # `load_pkd` nie wczyta, a ukośnik byłby gorszy: w stylu cudzysłowowym YAML
    # **interpretuje** sekwencje, więc zapis `\t` w nazwie stałby się tabulatorem po cichu.
    # Błąd wyszedłby po całym ręcznym pobieraniu z GUS, czyli najpóźniej, jak się da.
    ciało = yaml.safe_dump(pary, allow_unicode=True, sort_keys=True, width=10**9)
    tekst = "\n".join(naglowek) + ciało
    # Kontrola powrotna: plik ma wczytać się z powrotem do tego samego, zanim ktoś na nim polegnie.
    if yaml.safe_load(tekst) != pary:
        raise SystemExit("Zapis nie odtwarza wejścia — nie nadpisuję słownika.")
    out.write_text(tekst, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zrodlo", type=Path, help="plik CSV albo XLSX z klasyfikacją (eksport GUS)")
    parser.add_argument("--kod", default=None, help="nazwa kolumny z kodem")
    parser.add_argument("--nazwa", default=None, help="nazwa kolumny z nazwą")
    parser.add_argument("--rocznik", default=DEFAULT_ROCZNIK, help="rocznik klasyfikacji")
    parser.add_argument("--podstawa", default=None, help="podstawa prawna do nagłówka")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    out = args.out or DANE / f"pkd{args.rocznik}.yaml"

    if not args.zrodlo.is_file():
        raise SystemExit(f"Nie ma pliku {args.zrodlo}.")

    pary: dict[str, str] = {}
    konflikty: list[str] = []
    for kod, nazwa in czytaj(args.zrodlo, args.kod, args.nazwa):
        if kod in pary and pary[kod] != nazwa:
            konflikty.append(f"{kod}: {pary[kod]!r} vs {nazwa!r}")
        pary[kod] = nazwa

    if konflikty:
        # Dwie różne nazwy dla jednego kodu znaczą, że plik wejściowy miesza roczniki albo
        # ma kolumnę inną, niż myślimy. Cicha wygrana ostatniego wiersza byłaby tu najgorsza.
        raise SystemExit("Sprzeczne nazwy dla tego samego kodu:\n  " + "\n  ".join(konflikty))
    if not pary:
        raise SystemExit(
            "Nie znalazłem ani jednej podklasy. Sprawdź, czy to właściwy plik i czy kolumna "
            "z kodem zawiera podklasy (np. 62.01.Z), a nie same działy."
        )

    suma = hashlib.sha256(args.zrodlo.read_bytes()).hexdigest()
    zapisz(
        pary,
        out=out,
        zrodlo=args.zrodlo,
        suma=suma,
        rocznik=args.rocznik,
        podstawa=args.podstawa,
    )
    print(f"Zapisano {len(pary)} podklas (PKD {args.rocznik}) do {out}")
    print(
        "Porównaj tę liczbę z klasyfikacją i wpisz ją do OCZEKIWANE_PODKLASY w "
        "tests/test_assistant_pkd_data.py, notując, skąd pochodzi."
    )
    print("Potem sprawdź kilkanaście kodów w wyszukiwarce GUS — to jedyne, co łapie błąd źródła.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
