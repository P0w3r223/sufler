"""Bramka numeracji ADR: jeden numer — jedna decyzja.

Powód istnienia jest empiryczny. Praca nad harnessem agenta i praca nad odczytem
Jiry/grafikiem Shifts szły równolegle na dwóch gałęziach; obie sięgnęły po wolne
numery `0056` i `0057`. Scalenie było bezkonfliktowe tekstowo — pliki mają różne
nazwy — więc git przepuścił kolizję bez słowa, a w drzewie zostały czterdzieści
dwa cytowania „ADR 0056/0057" wskazujące na dwie różne decyzje każde.

Test sprawdza też zgodność numeru w nagłówku z numerem w nazwie pliku. To ten
warunek łapie renumerację zrobioną w połowie: `git mv` bez poprawienia tytułu
zostawia dokument, który sam o sobie mówi co innego niż katalog.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ADR_DIR = Path(__file__).resolve().parents[1] / "docs" / "adr"

# To bramka REPOZYTORIUM, nie runtime'u. Obraz kopiuje `src/` i `tests/`, ale nie `docs/`,
# więc w kontenerze katalog decyzji po prostu nie istnieje — złapane budowaniem etapu `test`,
# które wywaliło się na pustej liście plików. Pomijamy zamiast osłabiać asercję: w drzewie
# roboczym pusty `docs/adr/` nadal ma być błędem.
pytestmark = pytest.mark.skipif(
    not _ADR_DIR.is_dir(),
    reason="katalog docs/adr/ nieobecny — bramka dotyczy drzewa repozytorium, nie obrazu",
)

# `0059-teams-shifts-schedule-read.md` → numer i reszta nazwy. Podkreślenie obok
# myślnika, bo `0050_seed_corpus_document_extraction.md` jest starszy niż konwencja.
_NAZWA_PLIKU = re.compile(r"^(\d{4})[-_][a-z0-9_-]+\.md$")

# Trzy konwencje tytułu żyją w tej serii obok siebie: `# 0059. Extended…`,
# `# 0009 — Meeting-note flow…` i `# ADR 0036 — Weekly worklog…`. Bramka pilnuje
# NUMERU, nie interpunkcji — ujednolicanie 25 przyjętych dokumentów kosztowałoby
# więcej niż wnosi, a numer da się odczytać z każdej z nich.
_NAGLOWEK = re.compile(r"^#\s*(?:ADR\s+)?(\d{4})\s*[.—-]")


def _pliki_adr() -> list[Path]:
    return sorted(p for p in _ADR_DIR.glob("*.md") if p.name != "README.md")


def test_katalog_adr_nie_jest_pusty() -> None:
    """Bramka bez materiału przechodzi zawsze — to sprawdzenie samej bramki."""
    assert _pliki_adr(), f"brak plików ADR w {_ADR_DIR}"


def test_nazwy_plikow_maja_ksztalt_nnnn_slug() -> None:
    zle = [p.name for p in _pliki_adr() if not _NAZWA_PLIKU.match(p.name)]
    assert not zle, f"nazwy poza wzorcem NNNN-slug.md: {zle}"


def test_kazdy_numer_nalezy_do_jednej_decyzji() -> None:
    """Dwa pliki o tym samym numerze czynią każde cytowanie tego numeru dwuznacznym."""
    wedlug_numeru: dict[str, list[str]] = {}
    for plik in _pliki_adr():
        dopasowanie = _NAZWA_PLIKU.match(plik.name)
        if dopasowanie is None:
            continue
        wedlug_numeru.setdefault(dopasowanie.group(1), []).append(plik.name)

    kolizje = {numer: nazwy for numer, nazwy in wedlug_numeru.items() if len(nazwy) > 1}
    assert not kolizje, f"numer ADR użyty więcej niż raz: {kolizje}"


def test_wejsciowe_dokumenty_nie_maja_martwych_odsylaczy_do_adr() -> None:
    """Renumeracja bez poprawienia cytowań daje odsyłacz do pliku, którego nie ma.

    Ta klasa błędu przeżyła renumerację `0056/0057` → `0059/0060`: w `README.md` poprawiony
    został wiersz o grafiku Shifts, a sąsiedni o odczycie Jiry — cytujący ten sam ADR —
    został pominięty. Pozostałe bramki tego modułu patrzą na nazwy plików i nagłówki,
    więc żadna nie mogła tego zobaczyć.

    Zakres to dokumenty WEJŚCIOWE. Wzajemne odsyłacze między samymi ADR-ami niosą dług
    historyczny (nagłówki `Related to:` wskazują decyzje wycofane albo nigdy niezapisane),
    którego porządkowanie jest osobną pracą — bramka zapalona na nim od pierwszego dnia
    uczyłaby ignorowania bramki.
    """
    korzen = _ADR_DIR.parents[1]
    odsylacz = re.compile(r"docs/adr/(\d{4}[-_][a-z0-9_-]+\.md)")
    martwe: list[str] = []
    for nazwa in ("README.md", "CHANGELOG.md"):
        dokument = korzen / nazwa
        if not dokument.is_file():
            continue
        for numer, linia in enumerate(dokument.read_text(encoding="utf-8").splitlines(), 1):
            for cel in odsylacz.findall(linia):
                if not (_ADR_DIR / cel).is_file():
                    martwe.append(f"{nazwa}:{numer} → docs/adr/{cel}")
    assert not martwe, f"odsyłacze do nieistniejących ADR: {martwe}"


def test_numer_w_naglowku_zgadza_sie_z_nazwa_pliku() -> None:
    rozjazdy: list[str] = []
    for plik in _pliki_adr():
        z_nazwy = _NAZWA_PLIKU.match(plik.name)
        pierwsza_linia = plik.read_text(encoding="utf-8").splitlines()[0]
        z_naglowka = _NAGLOWEK.match(pierwsza_linia)
        if z_nazwy is None or z_naglowka is None:
            rozjazdy.append(f"{plik.name}: nagłówek {pierwsza_linia!r} bez numeru")
        elif z_nazwy.group(1) != z_naglowka.group(1):
            rozjazdy.append(f"{plik.name}: nagłówek mówi {z_naglowka.group(1)}")
    assert not rozjazdy, f"numer w nagłówku rozjechany z nazwą pliku: {rozjazdy}"
