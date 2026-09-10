"""Zakaz gniazd na czas całej suity — trzeci obserwator granicy offline.

Reguła granic 3 (`docs/design/etap1_core.md`). Skan importów nie zobaczy zależności
przechodniej, kontrola manifestu nie zobaczy `socket` z biblioteki standardowej — dopiero
zakaz w czasie wykonania łapie to, co naprawdę próbuje się połączyć.

Sam zakaz też ma obserwatora: `tests/test_granice.py::test_zakaz_gniazd_naprawde_gryzie`.
Bez niego byłby to fixture, o którym wierzymy, że działa.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest

from tests.support import odmowa_polaczenia


@pytest.fixture(autouse=True)
def zakaz_gniazd(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Zamyka każdą drogę do połączenia na czas testu."""
    monkeypatch.setattr(socket.socket, "connect", odmowa_polaczenia)
    monkeypatch.setattr(socket.socket, "connect_ex", odmowa_polaczenia)
    monkeypatch.setattr(socket, "create_connection", odmowa_polaczenia)
    monkeypatch.setattr(socket, "getaddrinfo", odmowa_polaczenia)
    yield
