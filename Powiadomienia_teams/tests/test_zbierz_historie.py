"""Pomiar D1 (`scripts/zbierz_historie.py`) — bucketowanie historii, na syntetycznych danych.

Skrypt nie ma sieci w testach i mieć nie musi: cała rzecz, która może dać CICHO zły wynik, siedzi
w `_historia_osoby` — przypisaniu zmian do tygodni i dni. Wynik tego pomiaru rozstrzyga, czy
`reminders/wzorzec.py` podłączyć, czy skasować, więc pomyłka tutaj kosztuje decyzję, nie awarię.

Import po ścieżce, bo `scripts/` nie jest pakietem i nie ma go na `pythonpath` (`pyproject.toml`).
"""

from __future__ import annotations

import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from powiadomienia_teams.domain.models import Shift, TimeOff

UTC = timezone.utc
WAW = ZoneInfo("Europe/Warsaw")
_SKRYPT = Path(__file__).resolve().parent.parent / "scripts" / "zbierz_historie.py"


def _modul():
    spec = importlib.util.spec_from_file_location("zbierz_historie", _SKRYPT)
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture(scope="module")
def zh():
    if not _SKRYPT.exists():
        pytest.skip("brak scripts/zbierz_historie.py — poza kontekstem repozytorium; biegnie w CI")
    return _modul()


def test_zmiana_trafia_do_tygodnia_i_dnia_ROZPOCZECIA(zh):
    """Nocka z piątku na sobotę należy do PIĄTKU — ten sam niezmiennik co w całym kodzie.

    Gdyby liczyć po dniu zakończenia, sobota dostałaby fałszywy wpis, a piątek zniknął z alfabetu —
    i wzorzec zacząłby proponować weekend osobie, która nigdy w weekend nie pracowała.
    """
    nocka = Shift("u1", datetime(2026, 8, 28, 20, tzinfo=UTC), datetime(2026, 8, 29, 4, tzinfo=UTC))
    historia = zh._historia_osoby("u1", [nocka], [], [date(2026, 8, 24)], WAW)

    assert len(historia) == 1
    dni = historia[0].dni
    assert set(dni) == {4}, f"zmiana trafiła do dni {sorted(dni)} zamiast do piątku"


def test_porownanie_po_TOZSAMOSCI_nie_znak_w_znak(zh):
    """Graph nie obiecuje tej samej wielkości liter w różnych kolekcjach (fala 2).

    Rozjazd nie zapisałby tu niczego złego — zaniżyłby historię do zera, a pomiar powiedziałby
    „brak danych" o osobie z pełnym grafikiem. Cicho i w stronę złej decyzji.
    """
    zmiana = Shift(
        "U1-ABC", datetime(2026, 8, 25, 6, tzinfo=UTC), datetime(2026, 8, 25, 14, tzinfo=UTC)
    )
    historia = zh._historia_osoby("u1-abc", [zmiana], [], [date(2026, 8, 24)], WAW)
    assert historia[0].dni, "zmiana odsiana przez porównanie znak w znak"


def test_cudze_zmiany_nie_wchodza_do_historii(zh):
    zmiana = Shift(
        "ktos-inny", datetime(2026, 8, 25, 6, tzinfo=UTC), datetime(2026, 8, 25, 14, tzinfo=UTC)
    )
    assert not zh._historia_osoby("u1", [zmiana], [], [date(2026, 8, 24)], WAW)[0].dni


def test_urlop_znaczy_dzien_urlopu_a_nie_dzien_pracy(zh):
    """Dzień urlopowy jest BRAKIEM DANYCH dla swojego weekdaya, nie dniem wolnym z wyboru."""
    wolne = TimeOff(
        "u1",
        datetime(2026, 8, 26, 0, tzinfo=UTC),
        datetime(2026, 8, 27, 0, tzinfo=UTC),
        reason_id="r1",
    )
    historia = zh._historia_osoby("u1", [], [wolne], [date(2026, 8, 24)], WAW)
    assert historia[0].dni_urlopu == frozenset({2}), historia[0].dni_urlopu
    assert not historia[0].dni


def test_tygodnie_wracaja_w_kolejnosci_z_argumentu(zh):
    """`wnioskuj` wymaga historii OD NAJŚWIEŻSZEGO — odwrócenie kolejności psuje regułę świeżości
    i rozpoznanie alternacji, oba po cichu."""
    poniedzialki = [date(2026, 8, 24), date(2026, 8, 17), date(2026, 8, 10)]
    historia = zh._historia_osoby("u1", [], [], poniedzialki, WAW)
    assert [t.poczatek for t in historia] == poniedzialki
