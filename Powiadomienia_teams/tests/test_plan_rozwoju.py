"""Każda pozycja planu cytowana w kodzie ma wpis w `docs/plan-rozwoju.md`.

Kod cytuje plan czterdzieści razy jako uzasadnienie decyzji — „pozycja D5 planu zdejmie tę
własność", „N38 mówi »każda ścieżka domykająca temat«". Do 2026-09-07 dokumentu nie było wcale:
zaginął razem z drzewem roboczym, z którego zbudowano obraz 0.2.19, więc czytelnik trafiał na
identyfikator i nie miał gdzie sprawdzić, co znaczy.

Reguła iteruje po CYTATACH W KODZIE, nie po wpisach planu. Kierunek jest tu całą wartością: nowy
cytat nieznanej pozycji zapala test bez niczyjej pamięci.

**Kierunek odwrotny świadomie NIE jest sprawdzany.** Plan ma prawo zawierać pozycje, których kod
jeszcze nie realizuje — to jest plan, nie spis treści. Reguła żądająca cytatu dla każdego wpisu
kasowałaby z planu wszystko, co otwarte, czyli dokładnie to, po co plan istnieje.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PODPROJEKT = Path(__file__).resolve().parent.parent
_PLAN = _PODPROJEKT / "docs" / "plan-rozwoju.md"

#: Identyfikator pozycji: litera rodziny + najwyżej dwie cyfry. Dwucyfrowy limit odsiewa kody
#: `ruff` (`E501`, `C901`, `B008`), które w tych samych plikach występują w `noqa` i w prozie.
_POZYCJA = re.compile(r"\b([NABCDE][0-9]{1,2})\b")

#: Sekcja planu: `§7`, `§10.3`. Sekcje CUDZYCH dokumentów (architektura, dane osobowe) zostały
#: z kodu usunięte razem z odsyłaczami do nich — jeśli wrócą, ta reguła je zauważy.
_SEKCJA = re.compile(r"§\s?([0-9]+(?:\.[0-9]+)?)")


def _cytaty(wzorzec: re.Pattern[str]) -> dict[str, str]:
    """Cytowany token → pierwsze miejsce, w którym go widać (do treści komunikatu błędu)."""
    znalezione: dict[str, str] = {}
    for katalog in ("src", "tests"):
        for plik in sorted((_PODPROJEKT / katalog).rglob("*.py")):
            if plik.name == Path(__file__).name:
                continue
            for nr, wiersz in enumerate(plik.read_text(encoding="utf-8").splitlines(), 1):
                for token in wzorzec.findall(wiersz):
                    znalezione.setdefault(token, f"{plik.relative_to(_PODPROJEKT)}:{nr}")
    return znalezione


def _pomin_bez_planu() -> None:
    if not _PLAN.exists():
        pytest.skip("strażnik planu — poza kontekstem repozytorium (obraz); biegnie w CI")


def test_kazda_cytowana_pozycja_ma_wpis_w_planie():
    _pomin_bez_planu()
    plan = _PLAN.read_text(encoding="utf-8")
    cytaty = _cytaty(_POZYCJA)
    assert len(cytaty) > 20, (
        f"znaleziono tylko {len(cytaty)} pozycji planu w kodzie — czy konwencja zapisu się "
        f"zmieniła? reguła, która nic nie widzi, jest zielona z niewłaściwego powodu"
    )
    # Wpis w planie oznaczamy pogrubieniem identyfikatora — samo wystąpienie w zdaniu nie wystarczy,
    # bo pozycje wymieniają się nawzajem w opisach i wtedy każda „miałaby wpis".
    w_planie = set(re.findall(r"\*\*([NABCDE][0-9]{1,2})\*\*", plan))
    brak = sorted(
        f"{poz} (cytowane w {gdzie})" for poz, gdzie in cytaty.items() if poz not in w_planie
    )
    assert brak == [], brak


def test_kazda_cytowana_sekcja_ma_wpis_w_planie():
    _pomin_bez_planu()
    plan = _PLAN.read_text(encoding="utf-8")
    w_planie = set(re.findall(r"\*\*§([0-9]+(?:\.[0-9]+)?)\*\*", plan))
    brak = sorted(
        f"§{sek} (cytowana w {gdzie})"
        for sek, gdzie in _cytaty(_SEKCJA).items()
        if sek not in w_planie
    )
    assert brak == [], brak
