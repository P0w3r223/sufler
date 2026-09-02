"""Bezpieczeństwo: sekrety nie wyciekają przez ``repr``/``str`` obiektów ustawień.

Klucze i tokeny (klucz Claude API, hasło bota Teams, PAT GitHuba, token Jiry) to dane poufne.
Przypadkowe zalogowanie obiektu ustawień albo traceback ze startu drzwi nie może ich ujawnić —
dlatego pola sekretne mają ``field(repr=False)``.

Poprzednia wersja pliku wymieniała DWA pola z nazwiska (``AgentSettings.api_key``,
``TeamsSettings.app_password``), więc dwa pozostałe nośniki sekretu (``GithubSettings.token``,
``JiraSettings.token``) nie były pilnowane wcale, a piąty dopisany jutro nie byłby tym bardziej.
Dziś pola SZUKAMY refleksyjnie po nazwie i typie — nowy sekret bez ``repr=False`` zapala test
w chwili dopisania, a lista ``_ZNANE_SEKRETY`` pilnuje, że wyszukiwanie nadal cokolwiek znajduje.
"""

from __future__ import annotations

import dataclasses
import inspect
import re
import typing

import pytest
from tests.conftest import pochodzi_z_config, przestrzen_config

# Nazwy pól niosących sekret. Wzorzec celowo szeroki (``*_token``, ``token``, ``api_key``,
# ``*password*``, ``*secret*``), zawężony potem typem ``str`` — ``tokens_file``,
# ``token_cache_path`` (ścieżki) i ``max_tokens`` (liczba) sekretem nie są.
_NAZWA_SEKRETU = re.compile(r"(^|_)(token|api_key|password|passwd|secret)($|_)")

# Nośniki sekretu znane z przeglądu 2026-08-17 — refleksja MUSI je znaleźć.
_ZNANE_SEKRETY = {
    "AgentSettings.api_key",
    "TeamsSettings.app_password",
    "GithubSettings.token",
    "JiraSettings.token",
}

_WARTOSC = "sekret-nie-do-logu-8f3a1c"


def _pola_sekretne() -> list[tuple[str, type, dataclasses.Field]]:
    znalezione: list[tuple[str, type, dataclasses.Field]] = []
    for name, obj in przestrzen_config().items():
        if not (inspect.isclass(obj) and dataclasses.is_dataclass(obj)):
            continue
        if not pochodzi_z_config(obj):
            continue
        hints = typing.get_type_hints(obj)
        for field in dataclasses.fields(obj):
            if _NAZWA_SEKRETU.search(field.name) and hints[field.name] is str:
                znalezione.append((name, obj, field))
    return znalezione


def _identyfikatory() -> list[str]:
    return [f"{cls_name}.{field.name}" for cls_name, _cls, field in _pola_sekretne()]


def _znajdz(identyfikator: str) -> tuple[type, dataclasses.Field]:
    cls_name, field_name = identyfikator.split(".")
    for name, cls, field in _pola_sekretne():
        if name == cls_name and field.name == field_name:
            return cls, field
    raise AssertionError(f"nie ma pola {identyfikator}")


def test_refleksja_znajduje_znane_nosniki_sekretu() -> None:
    """Sonda samej sondy: gdyby wzorzec przestał pasować, testy niżej cichłyby bez śladu."""
    znalezione = set(_identyfikatory())
    assert znalezione >= _ZNANE_SEKRETY, (
        f"wyszukiwanie pól sekretnych przestało widzieć: {sorted(_ZNANE_SEKRETY - znalezione)}"
    )


@pytest.mark.parametrize("identyfikator", sorted(set(_identyfikatory())))
def test_pole_sekretne_ma_repr_false(identyfikator: str) -> None:
    """``field(repr=False)`` to jedyne, co stoi między sekretem a logiem/tracebackiem."""
    _cls, field = _znajdz(identyfikator)

    assert field.repr is False, (
        f"{identyfikator} trafia do repr() obiektu ustawień — dodaj field(repr=False)"
    )


@pytest.mark.parametrize("identyfikator", sorted(set(_identyfikatory())))
def test_wartosc_sekretu_nie_pojawia_sie_w_tekstowej_postaci_obiektu(identyfikator: str) -> None:
    """Sprawdzamy WSZYSTKIE trzy drogi do logu: ``repr``, ``str`` i f-string (``format``).

    ``str`` dataklasy woła ``repr``, ale to szczegół implementacji Pythona, a nie deklaracja —
    log pisze się częściej przez ``f"{settings}"`` niż przez ``repr``.
    """
    cls, field = _znajdz(identyfikator)
    instancja = cls(**{field.name: _WARTOSC})

    assert _WARTOSC not in repr(instancja)
    assert _WARTOSC not in str(instancja)
    assert _WARTOSC not in f"{instancja}"


@pytest.mark.parametrize("identyfikator", sorted(set(_identyfikatory())))
def test_sekret_zostaje_dostepny_dla_kodu(identyfikator: str) -> None:
    """Kontrast: ukrycie dotyczy WYŁĄCZNIE reprezentacji — adapter musi dostać token do żądania.

    Bez tej sondy „naprawa" polegająca na wyzerowaniu pola przechodziłaby jako sukces.
    """
    cls, field = _znajdz(identyfikator)

    assert getattr(cls(**{field.name: _WARTOSC}), field.name) == _WARTOSC
