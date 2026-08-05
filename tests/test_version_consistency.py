"""Bramka spójności wersji: pakiet i obraz mówią jedną liczbę.

Powód jest empiryczny. Przy wydaniu 1.5.0 wersja była rozjechana w TRZY wartości na
siedmiu miejscach: pakiet 1.3.2 (``pyproject.toml``, ``__init__.py``, ``uv.lock``),
obraz 1.4.0 (``ARG WERSJA`` i komentarz w Dockerfile), compose deweloperski 1.3.0.
CHANGELOG sam zdiagnozował przyczynę: podbicie pakietu i podbicie obrazu były osobnymi
czynnościami, a nic ich ze sobą nie porównywało.

Porównanie ``pyproject`` ↔ ``__version__`` biegnie ZAWSZE, także w obrazie — oba pliki
są w ``/app``. Pliki wdrożeniowe (``deploy/``) do obrazu nie wjeżdżają, więc ich część
sprawdzamy tylko wtedy, gdy istnieją; w CI i na maszynie deweloperskiej istnieją zawsze.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import workmate

_KORZEN = Path(__file__).resolve().parents[1]
_PYPROJECT = _KORZEN / "pyproject.toml"
_DOCKERFILE = _KORZEN / "deploy" / "docker" / "Dockerfile"
_COMPOSE = _KORZEN / "deploy" / "docker" / "docker-compose.yml"

# `version = "1.5.0"` z sekcji [project]. Czytamy regexem, a nie `tomllib`, bo pakiet
# deklaruje `requires-python = ">=3.10"`, a `tomllib` jest dopiero od 3.11 — import
# wywaliłby całą kolekcję testów na najniższej wspieranej wersji.
_WERSJA_PAKIETU = re.compile(r'^version\s*=\s*"(\d+\.\d+\.\d+)"', re.M)
_ARG_WERSJA = re.compile(r"^ARG\s+WERSJA=(\d+\.\d+\.\d+)", re.M)
_OBRAZ = re.compile(r"workmate:(\d+\.\d+\.\d+)")
_WERSJA_BUILD = re.compile(r"WERSJA:\s*\"(\d+\.\d+\.\d+)\"")


def _wersja_pakietu() -> str:
    dopasowanie = _WERSJA_PAKIETU.search(_PYPROJECT.read_text(encoding="utf-8"))
    assert dopasowanie is not None, 'brak `version = "N.N.N"` w pyproject.toml'
    return dopasowanie.group(1)


def test_pyproject_zgodny_z_dunder_version() -> None:
    """Jedyna para dostępna także w obrazie — dlatego bez pominięcia."""
    assert workmate.__version__ == _wersja_pakietu()


@pytest.mark.skipif(not _DOCKERFILE.is_file(), reason="deploy/ nie wjeżdża do obrazu")
def test_dockerfile_buduje_te_sama_wersje() -> None:
    tresc = _DOCKERFILE.read_text(encoding="utf-8")
    oczekiwana = _wersja_pakietu()

    dopasowanie = _ARG_WERSJA.search(tresc)
    assert dopasowanie is not None, "brak `ARG WERSJA=N.N.N` w deploy/docker/Dockerfile"
    assert dopasowanie.group(1) == oczekiwana

    # Komentarze z komendą budowania też niosą tag — operator kopiuje je wprost do powłoki.
    rozjazdy = sorted({w for w in _OBRAZ.findall(tresc) if w != oczekiwana})
    assert not rozjazdy, f"Dockerfile wymienia obraz w wersji {rozjazdy}, pakiet ma {oczekiwana}"


@pytest.mark.skipif(not _COMPOSE.is_file(), reason="deploy/ nie wjeżdża do obrazu")
def test_compose_deweloperski_zgodny_z_pakietem() -> None:
    tresc = _COMPOSE.read_text(encoding="utf-8")
    oczekiwana = _wersja_pakietu()

    znalezione = sorted(set(_OBRAZ.findall(tresc)) | set(_WERSJA_BUILD.findall(tresc)))
    assert znalezione, "compose deweloperski nie wymienia żadnej wersji obrazu"
    assert znalezione == [oczekiwana], f"compose mówi {znalezione}, pakiet ma {oczekiwana}"
