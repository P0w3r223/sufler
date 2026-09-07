"""Numer wersji mieszka w pięciu miejscach — bramka pilnuje, żeby mówiły to samo.

Nic nie wywodzi ich jedne z drugich. Do 2026-09-07 docstring `__init__.py` twierdził, że robi to
`[tool.hatch.version]` plus skrypt `check_versions` — obie rzeczy nieprawdziwe, i przez to rozjazd
żył miesiącami: kod mówił `0.2.19`, `docker-compose.yml` i `build-image.sh` mówiły `0.2.1`,
a u klienta stał obraz `0.2.20`.

Rozjazd nie jest kosmetyczny: `image:` w compose przestaje jednoznacznie mówić, CO działa na
serwerze, a etykieta obrazu (`org.opencontainers.image.version`) jest jedynym sposobem, żeby to
ustalić po fakcie. Reguła iteruje po MIEJSCACH DEKLARACJI, więc szóste dopisane jutro trzeba
świadomie dopisać także tutaj.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from powiadomienia_teams import __version__

_PODPROJEKT = Path(__file__).resolve().parent.parent

#: (plik, wzorzec z grupą 1 = numer wersji). Kolejność bez znaczenia; komplet ma znaczenie.
_MIEJSCA = (
    ("pyproject.toml", re.compile(r'^version = "([^"]+)"', re.M)),
    ("Dockerfile", re.compile(r"^ARG WERSJA=([0-9][^\s]*)", re.M)),
    ("deploy/docker-compose.yml", re.compile(r"^\s*image: powiadomienia-teams:([^\s]+)", re.M)),
    ("scripts/build-image.sh", re.compile(r'^WERSJA="\$\{WERSJA:-([^}]+)\}"', re.M)),
)


def test_wszystkie_miejsca_deklaruja_te_sama_wersje():
    znalezione = {"src/powiadomienia_teams/__init__.py": __version__}
    for nazwa, wzorzec in _MIEJSCA:
        sciezka = _PODPROJEKT / nazwa
        if not sciezka.exists():
            pytest.skip(f"brak {nazwa} — poza kontekstem repozytorium (obraz); biegnie w CI")
        dopasowanie = wzorzec.search(sciezka.read_text(encoding="utf-8"))
        assert dopasowanie, f"{nazwa}: nie znalazłem deklaracji wersji — czy zmienił się kształt?"
        znalezione[nazwa] = dopasowanie.group(1)

    assert len(znalezione) == len(_MIEJSCA) + 1, "ubyło miejsc deklaracji — reguła mierzy mniej"
    assert len(set(znalezione.values())) == 1, znalezione
