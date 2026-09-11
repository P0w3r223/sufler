"""Rozdział twierdzeń od materiału, na którym powstały.

Twierdzenie o odpisie wolno postawić wyłącznie wobec pliku dostarczonego przez operatora.
Odpis syntetyczny ćwiczy kod i **nie jest dowodem** — bez tego rozdziału etap 2 zbudowany na
materiale własnym wyglądałby jak etap zbudowany na pomiarach.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

KORZEN = Path(__file__).resolve().parent.parent
PLIK_TWIERDZEN = KORZEN / "tests" / "fixtures" / "odpis_traits.yaml"
POMIARY = KORZEN / "docs" / "pomiary.md"
WYMAGANE_POLA = ("kod", "plik", "sha256", "data", "dostarczyl")


def _twierdzenia() -> dict[str, Any]:
    return dict(yaml.safe_load(PLIK_TWIERDZEN.read_text(encoding="utf-8")))


def test_warstwa_jest_jedyna_dopuszczalna() -> None:
    """Plik mówi o odpisie, nigdy o API. Inna warstwa = inne twierdzenie i inny plik."""
    assert _twierdzenia()["warstwa"] == "plik_odpisu"


def test_kazde_twierdzenie_cytuje_swoje_zrodlo() -> None:
    for wpis in _twierdzenia()["wlasnosci"]:
        brakujace = [pole for pole in WYMAGANE_POLA if not wpis.get(pole)]
        assert not brakujace, f"twierdzenie {wpis!r} nie cytuje {brakujace}"


def test_zaden_odpis_syntetyczny_nie_jest_cytowany() -> None:
    """Materiał własny nie może stać się dowodem przez pomyłkę w nazwie pliku."""
    for wpis in _twierdzenia()["wlasnosci"]:
        nazwa = str(wpis["plik"]).lower()
        assert "syntet" not in nazwa, f"{nazwa} to materiał własny, nie dowód"


def test_liczba_pomiarow_w_dokumencie_zgadza_sie_z_plikiem() -> None:
    """Licznik w `docs/pomiary.md` nie może zostać z tyłu ani pobiec do przodu.

    To jest cały mechanizm utrzymujący ten dokument w zgodzie z rzeczywistością: dopisanie
    zmierzonej własności bez podniesienia licznika zapala bramkę, i odwrotnie.
    """
    tresc = POMIARY.read_text(encoding="utf-8")
    dopasowanie = re.search(r"^zmierzonych-wlasnosci:\s*(\d+)\s*$", tresc, re.MULTILINE)

    assert dopasowanie is not None, "docs/pomiary.md nie deklaruje liczby zmierzonych własności"
    assert int(dopasowanie.group(1)) == len(_twierdzenia()["wlasnosci"])
