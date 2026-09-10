"""Bramka kolejności kroków w CI — bramka jakości ma raportować KOMPLET, nie pierwszy błąd.

Powód jest empiryczny i opisany w samym ``ci.yml``: od 2026-07-30 jeden padający test przykrywał
wszystkie kroki po sobie, więc ``ruff``, ``ruff format``, ``mypy`` i ``lint-imports`` nie
wykonywały się WCALE. Skutkiem były trzy pliki niezgodne z ``ruff format`` przy zielonym CI —
wychodziło to dopiero, gdy ktoś uruchomił formater lokalnie i zobaczył w diffie pliki, których
nie dotykał. Komentarz w workflow diagnozował mechanizm, ale nic go nie pilnowało.

Sonda mierzy WŁASNOŚĆ, nie zapis: każda bramka jakości albo stoi przed testami, albo ma warunek
uruchamiający ją niezależnie od wyniku wcześniejszych kroków.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_CI = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"

# Ten sam wzorzec co w `tests/test_version_consistency.py`: pakiet biegnie także WEWNĄTRZ obrazu
# (etap `test` Dockerfile'a), a obraz nie wozi konfiguracji CI — bez tej bramki cała ta grupa
# wywracała budowanie obrazu na `FileNotFoundError` przy kolekcji. Sondy mają sens tylko tam,
# gdzie jest pełne repozytorium, czyli w jobie „Bramka jakości".
pytestmark = pytest.mark.skipif(not _CI.is_file(), reason=".github/ nie wjeżdża do obrazu")

# Kroki, których wynik jest osobną informacją o jakości — żaden nie może przepaść przez to,
# że wcześniejszy był czerwony.
_BRAMKI = (
    "Lint (ruff)",
    "Format (ruff)",
    "Typy (mypy)",
    "Granice architektoniczne (import-linter)",
)
_TESTY = "Testy"


@pytest.fixture(scope="module")
def kroki() -> list[dict]:
    workflow = yaml.safe_load(_CI.read_text(encoding="utf-8"))
    return workflow["jobs"]["quality-gate"]["steps"]


def _indeks(kroki: list[dict], nazwa: str) -> int:
    for i, krok in enumerate(kroki):
        if krok.get("name") == nazwa:
            return i
    raise AssertionError(f"brak kroku {nazwa!r} w bramce jakości ci.yml")


def _biegnie_mimo_wczesniejszej_porazki(krok: dict) -> bool:
    warunek = str(krok.get("if", ""))
    return "always()" in warunek or "cancelled()" in warunek


@pytest.mark.parametrize("nazwa", _BRAMKI)
def test_bramka_statyczna_nie_moze_zostac_przykryta_przez_testy(kroki: list[dict], nazwa: str):
    """Albo przed testami, albo z warunkiem ``always()``/``!cancelled()``. Trzeciej drogi nie ma."""
    krok = kroki[_indeks(kroki, nazwa)]
    przed_testami = _indeks(kroki, nazwa) < _indeks(kroki, _TESTY)

    assert przed_testami or _biegnie_mimo_wczesniejszej_porazki(krok), (
        f"krok {nazwa!r} stoi po testach i nie ma warunku uruchamiającego go mimo porażki — "
        "jeden czerwony test znowu przykryje bramki statyczne (incydent 2026-07-30)."
    )


def test_testy_nie_gina_gdy_bramka_statyczna_jest_czerwona(kroki: list[dict]):
    """Odwrotny kierunek: przestawienie kolejności nie może zamienić przykrycia na przykrycie."""
    krok = kroki[_indeks(kroki, _TESTY)]
    assert _biegnie_mimo_wczesniejszej_porazki(krok), (
        "krok 'Testy' stoi po bramkach statycznych bez warunku — czerwony lint ukryłby wynik."
    )


def test_warunek_krokow_nie_gubi_zawezenia_do_projektu_workmate(kroki: list[dict]):
    """``lint-imports`` dotyczy TYLKO rdzenia workmate — pod-projekty nie mają import-lintera.

    Dokładając warunek ``!cancelled()`` łatwo nadpisać istniejące ``if: matrix.name == 'workmate'``
    i puścić krok na wszystkich wpisach macierzy; wtedy trzy z czterech wpisów padają na braku
    konfiguracji.
    """
    krok = kroki[_indeks(kroki, "Granice architektoniczne (import-linter)")]
    assert "matrix.name" in str(krok.get("if", ""))
