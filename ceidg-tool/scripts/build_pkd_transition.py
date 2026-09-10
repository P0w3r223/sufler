#!/usr/bin/env python
"""Buduje `ceidg_tool/data/pkd2007_2025.yaml` z **oficjalnego** klucza przejścia GUS.

Po co: rejestr jest w połowie przejścia z PKD 2007 na PKD 2025 (do 31.12.2026), a filtr `pkd`
dopasowuje kod tak, jak zapisano go w rekordzie. Zmierzone 2026-09-07 (`docs/decisions.md`):
58,6 % rekordów nadal ma kody 2007, a 8,6 % nie niesie żadnego kodu ze słownika PKD 2025.
Zapytanie o fryzjerów kodem `9621Z` sięga 17 % fryzjerów — bez błędu gdziekolwiek po drodze.
Tablica pozwala dołożyć do zapytania poprzedników z 2007 (ADR-0012).

Dlaczego skrypt, a nie plik pisany ręcznie albo przez model: dokładnie ten sam powód co przy
`build_pkd.py`. Tablica przekładu przepisana z pamięci przeszłaby wszystkie automatyczne
sprawdzenia — kanoniczność kodów, liczbę wpisów, zgodność z rocznikiem — i mogłaby być cicho
błędna w nazwach oraz w tym, które kody są niejednoznaczne. A to odwraca jedyną kontrolę
operatora: ekran potwierdzenia pokazuje **nazwę**, więc zła nazwa sprawia, że ekran się zgadza.

Czego skrypt **nie** zapisuje: podziału na rozszerzenia „czyste" i „niejednoznaczne". Ten podział
`pkdmap.py` liczy z zawartości pliku (rozgałęzienie kodu 2007), więc jest własnością danych,
sprawdzalną przez przebudowę, a nie liczbą w czyimś komentarzu.

Skąd wziąć wejście: `https://klasyfikacje.stat.gov.pl/Pkd2025` → klucze powiązań PKD 2007–2025.
Plik z GUS jest prawdziwym BIFF8, którego `openpyxl` nie czyta — przekonwertuj LibreOffice'em
(świadomie nie Excelem: jego heurystyki importu robią z `01.11.Z` datę).

Użycie:

    "C:/Program Files/LibreOffice/program/soffice.exe" --headless \\
        --convert-to xlsx --outdir PKD PKD/KluczePKD_2007_2025.xls
    PYTHONUTF8=1 python scripts/build_pkd_transition.py PKD/KluczePKD_2007_2025.xlsx
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import openpyxl
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ceidg_tool.criteria import normalize_pkd  # noqa: E402

DANE = Path(__file__).resolve().parent.parent / "ceidg_tool" / "data"
DOMYSLNE_WYJSCIE = DANE / "pkd2007_2025.yaml"
SLOWNIK_2025 = DANE / "pkd2025.yaml"

# Arkusz i kolumny w kluczu GUS. Arkusz „2007-2025" niesie wszystkie poziomy klasyfikacji;
# bierzemy wyłącznie poziom 5, czyli podklasę, bo tylko podklasa jest wartością `Criteria.pkd`.
ARKUSZ = "2007-2025"
POZIOM_PODKLASY = "5"
KOL_POZIOM = "Poziom"
KOL_KOD_2007 = "Symbol PKD 2007"
KOL_NAZWA_2007 = "Nazwa grupowania PKD 2007"
KOL_KOD_2025 = "Symbol PKD 2025"
KOL_NAZWA_2025 = "Nazwa grupowania PKD 2025"
WYMAGANE_KOLUMNY = (KOL_POZIOM, KOL_KOD_2007, KOL_NAZWA_2007, KOL_KOD_2025, KOL_NAZWA_2025)

# Arkusze GUS zaczynają się od wierszy objaśniających, więc nagłówka szukamy, a nie zakładamy.
NAGLOWEK_SZUKAJ_W = 10

PODSTAWA_PRAWNA = "Dz.U. 2024 poz. 1936 (rozporządzenie RM z 18.12.2024)"
WYGASA = "2026-12-31"


def wiersze(path: Path) -> Iterator[dict[str, str]]:
    """Wiersze arkusza jako słowniki. Nagłówek wyszukiwany po wymaganych kolumnach."""
    if path.suffix.lower() == ".xls":
        raise SystemExit(
            f"{path.name} jest w starym formacie .xls, którego nie czytam. Przekonwertuj:\n"
            '  "C:/Program Files/LibreOffice/program/soffice.exe" --headless '
            f"--convert-to xlsx --outdir {path.parent} {path}\n"
            "a potem podaj powstały plik .xlsx."
        )
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if ARKUSZ not in wb.sheetnames:
        raise SystemExit(f"{path.name} nie ma arkusza {ARKUSZ!r}; są: {', '.join(wb.sheetnames)}.")
    surowe: list[tuple[Any, ...]] = list(wb[ARKUSZ].iter_rows(values_only=True))

    naglowek: list[str] | None = None
    start = 0
    for i, wiersz in enumerate(surowe[:NAGLOWEK_SZUKAJ_W]):
        komorki = [str(c).strip() if c is not None else "" for c in wiersz]
        if set(WYMAGANE_KOLUMNY) <= set(komorki):
            naglowek, start = komorki, i + 1
            break
    if naglowek is None:
        raise SystemExit(
            f"W pierwszych {NAGLOWEK_SZUKAJ_W} wierszach {path.name} nie ma wszystkich kolumn: "
            f"{', '.join(WYMAGANE_KOLUMNY)}."
        )
    for wiersz in surowe[start:]:
        yield {
            nazwa: (str(wartosc).strip() if wartosc is not None else "")
            for nazwa, wartosc in zip(naglowek, wiersz, strict=False)
        }


def _kod(surowy: str) -> str | None:
    """Kanoniczna podklasa albo `None` dla sekcji, działu, grupy i klasy."""
    try:
        return normalize_pkd(surowy)
    except ValueError:
        return None


def zbierz(
    path: Path, kody2025: set[str]
) -> tuple[
    dict[str, list[str]],
    dict[str, str],
    dict[str, str],
    list[str],
    list[str],
]:
    """Zwraca (poprzednicy, nazwy 2007, nazwy 2025, ostrzeżenia).

    Poprzednik to kod PKD 2007, którego **trzeba dołożyć** do zapytania, żeby sięgnąć rekordów
    jeszcze nieprzeniesionych. Pomijamy dwa przypadki, w których dokładanie niczego nie zmienia:
    kod identyczny z kodem 2025 (łańcuch się nie zmienił, więc filtr już go obejmuje) oraz kod,
    którego w ogóle nie ma w naszym słowniku 2025 — ten drugi przypadek to sygnał, nie cisza,
    więc trafia do ostrzeżeń.
    """
    poprzednicy: dict[str, set[str]] = defaultdict(set)
    nazwy2007: dict[str, str] = {}
    nazwy2025: dict[str, str] = {}
    ostrzezenia: list[str] = []
    konflikty: list[str] = []
    spoza: set[str] = set()
    zywe: set[str] = set()

    for wiersz in wiersze(path):
        if wiersz.get(KOL_POZIOM) != POZIOM_PODKLASY:
            continue
        stary = _kod(wiersz.get(KOL_KOD_2007, ""))
        nowy = _kod(wiersz.get(KOL_KOD_2025, ""))
        if stary is None or nowy is None:
            continue
        if nowy not in kody2025:
            spoza.add(nowy)
            continue

        nazwa_stara = " ".join(wiersz.get(KOL_NAZWA_2007, "").split())
        nazwa_nowa = " ".join(wiersz.get(KOL_NAZWA_2025, "").split())
        # Dwie różne nazwy dla jednego kodu znaczą, że plik miesza roczniki albo że kolumna jest
        # inna, niż myślimy. Cicha wygrana ostatniego wiersza byłaby tu najgorszym wynikiem.
        for kod, nazwa, gdzie in ((stary, nazwa_stara, nazwy2007), (nowy, nazwa_nowa, nazwy2025)):
            if not nazwa:
                continue
            if kod in gdzie and gdzie[kod] != nazwa:
                konflikty.append(f"{kod}: {gdzie[kod]!r} vs {nazwa!r}")
            gdzie[kod] = nazwa

        if stary == nowy:
            # Kod się nie zmienił, więc filtr 2025 obejmuje go dosłownie — nie ma czego dokładać.
            continue
        poprzednicy[nowy].add(stary)
        if stary in kody2025:
            # Kod 2007, który w PKD 2025 **nadal istnieje, ale znaczy co innego**. Do przeglądu
            # 2026-09-07 takie mapowania były odrzucane z uzasadnieniem „filtr 2025 już go
            # obejmuje" — nieprawdziwym: filtr obejmuje wtedy *rekord*, a nie *branżę, o którą
            # pyta operator*. Odrzucenie zabierało poprzedników 93 kodom 2025, w tym klubom
            # fitness (`9313Z` ← `8551Z`), piekarniom (`1071Z` ← `1086Z`) i uprawie warzyw.
            # To zwykłe rozszerzenie niejednoznaczne, tylko drugiego rodzaju: dokładając
            # `8551Z`, bierzemy też dzisiejsze „Pozostałe formy edukacji sportowej".
            zywe.add(stary)

    if konflikty:
        raise SystemExit("Sprzeczne nazwy dla tego samego kodu:\n  " + "\n  ".join(konflikty))
    if spoza:
        ostrzezenia.append(
            f"{len(spoza)} kodów PKD 2025 z klucza nie ma w {SLOWNIK_2025.name} "
            f"(np. {', '.join(sorted(spoza)[:5])}) — pominięte."
        )
    bez_nazwy = sorted(k for kody in poprzednicy.values() for k in kody if not nazwy2007.get(k))
    if bez_nazwy:
        # Nazwa jest jedyną kontrolą operatora nad tym, czy dostał właściwą branżę. Kod bez
        # nazwy nie może trafić na ekran, więc lepiej nie zbudować pliku niż zbudować niepełny.
        raise SystemExit(
            "Kody PKD 2007 bez nazwy w kluczu: " + ", ".join(bez_nazwy[:10]) + ". "
            "Ekran potwierdzenia pokazuje nazwę, więc bez niej rozszerzenie jest nieczytelne."
        )
    return (
        {k: sorted(v) for k, v in sorted(poprzednicy.items())},
        nazwy2007,
        nazwy2025,
        sorted(zywe),
        ostrzezenia,
    )


def zapisz(
    poprzednicy: dict[str, list[str]],
    nazwy2007: dict[str, str],
    nazwy2025: dict[str, str],
    zywe: list[str],
    slownik2025: dict[str, str],
    *,
    out: Path,
    zrodlo: Path,
    suma: str,
) -> None:
    """Zapisuje tablicę z prowenienacją w nagłówku i kontrolą powrotną przed nadpisaniem."""
    potrzebne2007 = {k for kody in poprzednicy.values() for k in kody}
    # Nazwy 2025 są potrzebne dla kodów będących kluczami **oraz** dla tych, do których
    # poprzednik prowadzi dodatkowo — to z nich powstaje zdanie „obejmuje też…".
    rozgalezienie: dict[str, set[str]] = defaultdict(set)
    for nowy, stare in poprzednicy.items():
        for stary in stare:
            rozgalezienie[stary].add(nowy)
    # Kody „żywe" też trafiają na ekran — jako dzisiejsze znaczenie dokładanego kodu — więc ich
    # nazwy muszą być w pliku. Bierzemy je z `pkd2025.yaml`, czyli z tego samego oficjalnego
    # źródła co reszta klasyfikacji, a nie z kolumny klucza, gdzie kod stoi w roli poprzednika.
    potrzebne2025 = set(poprzednicy) | {c for cele in rozgalezienie.values() for c in cele}
    for kod in zywe:
        nazwy2025.setdefault(kod, slownik2025.get(kod, ""))
    potrzebne2025 |= set(zywe)

    # Symetrycznie do nazw 2007: brak nazwy 2025 też jest błędem, a nie powodem do pominięcia
    # wpisu. Nazwa kodu z „obejmuje też" trafia na ekran potwierdzenia dokładnie tak samo jak
    # nazwa poprzednika, więc `if k in nazwy2025` zamieniłoby brakującą nazwę w gołe „9622Z"
    # na ekranie — czyli w tę samą awarię, przed którą broni sprawdzenie kilka linii wyżej.
    bez_nazwy_2025 = sorted(k for k in potrzebne2025 if not nazwy2025.get(k))
    if bez_nazwy_2025:
        raise SystemExit(
            "Kody PKD 2025 bez nazwy w kluczu: " + ", ".join(bez_nazwy_2025[:10]) + ". "
            "Te nazwy trafiają na ekran potwierdzenia, więc bez nich tablica jest nieczytelna."
        )
    dane = {
        "poprzednicy": poprzednicy,
        # Kody PKD 2007, które w PKD 2025 nadal istnieją, ale znaczą co innego. Dokładając taki
        # kod, bierzemy też jego dzisiejszą branżę — `pkdmap` mówi o tym operatorowi wprost.
        "zywe_2025": zywe,
        "nazwy_2007": {k: nazwy2007[k] for k in sorted(potrzebne2007)},
        "nazwy_2025": {k: nazwy2025[k] for k in sorted(potrzebne2025)},
    }
    naglowek = [
        "# Klucz przejścia PKD 2007 → PKD 2025 — poprzednicy podklas (ADR-0012).",
        "#",
        "# Do czego służy: rejestr trzyma przy rekordzie jeden rocznik klasyfikacji, a filtr",
        "# `pkd` dopasowuje kod tak, jak go zapisano. Kod z PKD 2025 nie sięga więc rekordów",
        "# jeszcze nieprzeniesionych. `poprzednicy[kod2025]` to kody PKD 2007, które trzeba",
        "# dołożyć do zapytania, żeby je objąć.",
        "#",
        f"# Podstawa prawna: {PODSTAWA_PRAWNA}",
        "# Wyszukiwarka GUS: https://klasyfikacje.stat.gov.pl/Pkd2025",
        "#",
        f"# Zbudowane: {datetime.now(tz=UTC).date().isoformat()} przez "
        "scripts/build_pkd_transition.py",
        f"# Plik źródłowy: {zrodlo.name}",
        f"# SHA-256 źródła: {suma}",
        "#",
        f"# Wygasa: {WYGASA} — koniec okresu przejściowego. Po tej dacie zmierz udział",
        "# rocznika 2007 w świeżym raporcie dziennym (offline, zero żądań) i jeśli jest",
        "# znikomy, usuń ten plik razem z ceidg_tool/pkdmap.py i krokiem w ui/flow.py.",
        "#",
        "# Plik jest generowany. Nie edytuj go ręcznie — popraw źródło i zbuduj ponownie,",
        "# inaczej prowenienacja w tym nagłówku przestaje opisywać zawartość.",
        "",
    ]
    ciało = yaml.safe_dump(dane, allow_unicode=True, sort_keys=True, width=10**9)
    tekst = "\n".join(naglowek) + ciało
    if yaml.safe_load(tekst) != dane:
        raise SystemExit("Zapis nie odtwarza wejścia — nie nadpisuję tablicy.")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(tekst, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zrodlo", type=Path, help="klucz przejścia GUS (.xlsx po konwersji)")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--slownik", type=Path, default=None, help="słownik PKD 2025 do kontroli")
    args = parser.parse_args()
    out = args.out or DOMYSLNE_WYJSCIE
    slownik_path = args.slownik or SLOWNIK_2025

    if not args.zrodlo.is_file():
        raise SystemExit(f"Nie ma pliku {args.zrodlo}.")
    if not slownik_path.is_file():
        raise SystemExit(f"Nie ma słownika {slownik_path}. Zbuduj go najpierw: build_pkd.py.")

    slownik = yaml.safe_load(slownik_path.read_text(encoding="utf-8")) or {}
    kody2025 = {str(k).strip() for k in slownik}

    poprzednicy, nazwy2007, nazwy2025, zywe, ostrzezenia = zbierz(args.zrodlo, kody2025)
    if not poprzednicy:
        raise SystemExit(
            "Nie znalazłem ani jednego poprzednika. Sprawdź, czy to właściwy plik i czy arkusz "
            f"{ARKUSZ!r} ma wiersze poziomu {POZIOM_PODKLASY}."
        )

    suma = hashlib.sha256(args.zrodlo.read_bytes()).hexdigest()
    zapisz(
        poprzednicy,
        nazwy2007,
        nazwy2025,
        zywe,
        {str(k): str(v) for k, v in slownik.items()},
        out=out,
        zrodlo=args.zrodlo,
        suma=suma,
    )

    for ostrzezenie in ostrzezenia:
        print(f"Uwaga: {ostrzezenie}")
    # Podział czyste/niejednoznaczne liczymy tu wyłącznie po to, żeby go **pokazać**; do pliku
    # nie trafia, bo `pkdmap.py` wylicza go z tych samych danych przy wczytaniu.
    rozgalezienie: dict[str, int] = defaultdict(int)
    for stare in poprzednicy.values():
        for stary in stare:
            rozgalezienie[stary] += 1
    czyste = sum(1 for stare in poprzednicy.values() if all(rozgalezienie[s] == 1 for s in stare))
    print(f"Zapisano {len(poprzednicy)} kodów PKD 2025 z poprzednikami do {out}")
    print(f"  rozszerzenia czyste: {czyste}, niejednoznaczne: {len(poprzednicy) - czyste}")
    print(f"  kodów PKD 2007 w tablicy: {len(rozgalezienie)}")
    print(f"  z tego nadal żywych w PKD 2025 (o innym znaczeniu): {len(zywe)}")
    print("Liczby porównaj z docs/decisions.md — rozjazd znaczy, że zmieniło się źródło.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
