"""Deklarowana dolna wersja Pythona ma odpowiadać tej, na której cokolwiek biega.

``requires-python = ">=3.10"`` nie było testowane NIGDZIE: ``.python-version`` mówi 3.11, a oba
etapy obrazu stoją na ``python:3.11-slim``. Deklaracja szersza od pokrycia nie jest neutralna —
kosztowała obejście w ``tests/test_version_consistency.py``, gdzie ``pyproject.toml`` czytany
jest regexem zamiast ``tomllib`` (3.11+) „żeby nie wywrócić kolekcji na najniższej wspieranej
wersji".

Sondy niżej wiążą deklarację z ``.python-version`` i z obrazem bazowym — i to są JEDYNE dwa
źródła, o które warto ją opierać. CI nie jest tu niezależnym dowodem: macierz w
``.github/workflows/ci.yml`` ma oś pod-projektów (workmate / powiadomienia-teams /
claude-summary / ceidg-tool), a nie oś wersji Pythona; jedna wersja bierze się stamtąd, że
``setup-uv`` czyta ten sam ``.python-version``. Oś 3.11/3.12 w repozytorium JEST od 2026-09-10
(``.github/workflows/ceidg-tool.yml``), ale biegnie dla innego pakietu, z własnym
``requires-python`` — nie jest dowodem o tym. Zielony przebieg nie mówi więc nic o 3.10 —
mówi tyle, co plik, który już sprawdzamy.

Ten plik czyta ``pyproject.toml`` przez ``tomllib`` celowo: sam jest dowodem, że obejście
przestało być potrzebne.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

_KORZEN = Path(__file__).resolve().parents[2]
_PYPROJECT = _KORZEN / "pyproject.toml"
_PYTHON_VERSION = _KORZEN / ".python-version"
_DOCKERFILE = _KORZEN / "deploy" / "docker" / "Dockerfile"

# Jak w `tests/test_version_consistency.py`: pakiet biegnie też WEWNĄTRZ obrazu, a ani
# `.python-version`, ani `deploy/` tam nie wjeżdżają. Wiązanie deklaracji z tymi plikami ma sens
# wyłącznie w pełnym repozytorium — w obrazie nie ma z czym porównywać.
pytestmark = pytest.mark.skipif(
    not (_PYTHON_VERSION.is_file() and _DOCKERFILE.is_file()),
    reason=".python-version i deploy/ nie wjeżdżają do obrazu",
)

_OBRAZ_PYTHONA = re.compile(r"^FROM\s+python:(\d+\.\d+)", re.M)


def _dolna_granica() -> str:
    metadane = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    wymog = metadane["project"]["requires-python"]
    dopasowanie = re.fullmatch(r">=\s*(\d+\.\d+)", wymog.strip())
    assert dopasowanie is not None, f"nieoczekiwany zapis requires-python: {wymog!r}"
    return dopasowanie.group(1)


def test_requires_python_zgodne_z_wersja_srodowiska_deweloperskiego():
    """``.python-version`` steruje tym, co ``uv` instaluje lokalnie i w CI — to realne pokrycie."""
    assert _dolna_granica() == _PYTHON_VERSION.read_text(encoding="utf-8").strip()


def test_requires_python_zgodne_z_obrazem_floty():
    """Obraz to jedyne środowisko produkcyjne; deklaracja niższa niż obraz to wersja bez testów."""
    znalezione = sorted(set(_OBRAZ_PYTHONA.findall(_DOCKERFILE.read_text(encoding="utf-8"))))

    assert znalezione, "Dockerfile nie wymienia żadnego obrazu bazowego python:X.Y"
    assert znalezione == [_dolna_granica()], (
        f"Dockerfile buduje na Pythonie {znalezione}, pakiet deklaruje >={_dolna_granica()}. "
        "Albo zawęź requires-python, albo dołóż macierzy ci.yml OŚ wersji Pythona (dziś jej "
        "nie ma — wpisy to cztery pod-projekty, a wersja idzie z .python-version; oś w "
        "ceidg-tool.yml dotyczy innego pakietu)."
    )
