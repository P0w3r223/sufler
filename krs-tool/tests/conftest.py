"""Dwa zakazy na czas całej suity: gniazda i katalog danych operatora.

Reguła granic 3 (`docs/design/etap1_core.md`). Skan importów nie zobaczy zależności
przechodniej, kontrola manifestu nie zobaczy `socket` z biblioteki standardowej — dopiero
zakaz w czasie wykonania łapie to, co naprawdę próbuje się połączyć.

Sam zakaz też ma obserwatora: `tests/test_granice.py::test_zakaz_gniazd_naprawde_gryzie`.
Bez niego byłby to fixture, o którym wierzymy, że działa.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from krs_tool import cli
from tests.support import odmowa_polaczenia


@pytest.fixture(autouse=True)
def zakaz_gniazd(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Zamyka każdą drogę do połączenia na czas testu."""
    monkeypatch.setattr(socket.socket, "connect", odmowa_polaczenia)
    monkeypatch.setattr(socket.socket, "connect_ex", odmowa_polaczenia)
    monkeypatch.setattr(socket, "create_connection", odmowa_polaczenia)
    monkeypatch.setattr(socket, "getaddrinfo", odmowa_polaczenia)
    yield


@pytest.fixture(autouse=True)
def magazyn_poza_katalogiem_uzytkownika(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Path]:
    """Żaden test nie pisze do prawdziwego dziennika operatora.

    Zmierzone, nie teoretyczne: jeden test wołał `raport` bez `--magazyn`, więc każde
    uruchomienie suity dopisywało linię do `%LOCALAPPDATA%/krs-tool/dziennik.jsonl`
    i zostawiało tam ładunek. Dziennik jest **z założenia niekasowalny**, a ładunek to kopia
    odpisu, czyli dane osób w organach spółki — więc pomyłka w tę stronę jest trwała i dotyczy
    danych osobowych.

    Fixture podmienia domyślny magazyn **w warstwie poleceń**, bo to ona wiąże nazwę przy
    imporcie; podmiana w `krs_tool.magazyn` nic by nie dała i wyglądałaby na działającą.
    """
    zastepczy = tmp_path / "magazyn-testowy"
    monkeypatch.setattr(cli, "domyslny_magazyn", lambda: zastepczy)
    yield zastepczy
