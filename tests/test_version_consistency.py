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
import tomllib
from pathlib import Path

import pytest

import workmate

_KORZEN = Path(__file__).resolve().parents[1]
_PYPROJECT = _KORZEN / "pyproject.toml"
_DOCKERFILE = _KORZEN / "deploy" / "docker" / "Dockerfile"
_COMPOSE = _KORZEN / "deploy" / "docker" / "docker-compose.yml"
_README = _KORZEN / "README.md"

_ARG_WERSJA = re.compile(r"^ARG\s+WERSJA=(\d+\.\d+\.\d+)", re.M)
_OBRAZ = re.compile(r"workmate:(\d+\.\d+\.\d+)")
_WERSJA_BUILD = re.compile(r"WERSJA:\s*\"(\d+\.\d+\.\d+)\"")
_BADGE = re.compile(r"badge/wersja-(\d+\.\d+\.\d+)-")


def _wersja_pakietu() -> str:
    """``[project].version`` czytana ``tomllib`` — parserem formatu, nie wzorcem tekstowym.

    Do 2026-08-17 stał tu regex, uzasadniony deklaracją ``requires-python = ">=3.10"``
    (``tomllib`` wchodzi dopiero w 3.11, więc import wywróciłby kolekcję na najniższej
    wspieranej wersji). Deklaracja zeszła do ``>=3.11`` — bo 3.10 nie było testowane
    NIGDZIE — więc obejście straciło powód i zostaje zdjęte razem z nim.
    """
    metadane = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    wersja = metadane["project"]["version"]
    assert isinstance(wersja, str), 'brak `version = "N.N.N"` w [project] pyproject.toml'
    return wersja


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


def test_badge_w_readme_mowi_wersje_pakietu() -> None:
    """Badge też jest miejscem z zaszytą wersją — i zjechał o trzy wydania, zanim ktoś spojrzał.

    Przy podbiciu na 1.6.0 README mówiło jeszcze **1.3.2**. Bramka wyżej porównywała pakiet
    z ``__init__``, Dockerfile'em i compose'em deweloperskim, więc rozjazd nie miał gdzie się
    zapalić, a paczka wdrożeniowa sprawdza WŁASNĄ kopię (``image/context/app-README.md``, ta
    jedzie do obrazu jako ``/app/README.md``) — czyli inny plik. Dwie kopie, jedna bramkowana.

    ``README.md`` wjeżdża do obrazu, więc ta sonda biegnie ZAWSZE — jak para
    pakiet ↔ ``__version__``, a inaczej niż sondy plików z ``deploy/``.
    """
    znalezione = sorted(set(_BADGE.findall(_README.read_text(encoding="utf-8"))))
    # Rozdzielone od porównania, bo brak badge'a i badge rozjechany to DWIE różne usterki —
    # wspólna asercja mówiła na obie „badge mówi []", co nie naprowadza na przyczynę.
    assert znalezione, "README nie ma badge'a z wersją (wzorzec: badge/wersja-N.N.N-)"
    assert znalezione == [_wersja_pakietu()], (
        f"badge w README mówi {znalezione}, pakiet ma {_wersja_pakietu()}"
    )


@pytest.mark.skipif(not _COMPOSE.is_file(), reason="deploy/ nie wjeżdża do obrazu")
def test_compose_deweloperski_zgodny_z_pakietem() -> None:
    tresc = _COMPOSE.read_text(encoding="utf-8")
    oczekiwana = _wersja_pakietu()

    znalezione = sorted(set(_OBRAZ.findall(tresc)) | set(_WERSJA_BUILD.findall(tresc)))
    assert znalezione, "compose deweloperski nie wymienia żadnej wersji obrazu"
    assert znalezione == [oczekiwana], f"compose mówi {znalezione}, pakiet ma {oczekiwana}"
