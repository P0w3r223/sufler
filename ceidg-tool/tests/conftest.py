"""Wspólne atrapy: deterministyczny zegar, profil testowy, przykładowe rekordy z dokumentacji."""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import pytest

from ceidg_tool.apiprofile import ApiProfile
from ceidg_tool.config import forget_secrets


@pytest.fixture(autouse=True)
def clean_secret_registry() -> Iterator[None]:
    """Rejestr sekretów jest procesowy, więc bez sprzątania przeciekałby między testami.

    Test, który zarejestrował token, maskowałby go w asercjach kolejnego — i test sprawdzający,
    że coś **nie** jest maskowane, przechodziłby albo padał zależnie od kolejności uruchomienia.
    """
    forget_secrets()
    yield
    forget_secrets()


@pytest.fixture(autouse=True)
def no_name_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cała suita jest offline — żaden test nie rozwiązuje nazw (UZUPELNIENIE_01 §D).

    To jest siatka bezpieczeństwa, nie asercja o kodzie: gdyby jakikolwiek test zaczął naprawdę
    wychodzić do sieci, dowiemy się o tym tutaj — deterministycznie i lokalnie — zamiast
    z niestabilnego CI albo z rachunku za żądania na produkcji. Testy, które **badają**
    rozwiązywanie nazw (`tests/resilience/test_egress_allowlist.py`), podmieniają tę atrapę
    na własną, notującą; ten sam `monkeypatch` cofa obie w odwrotnej kolejności.
    """

    def refuse(host: object, *args: object, **kwargs: object) -> object:
        raise AssertionError(f"test próbował rozwiązać nazwę {host!r} — suita ma działać bez sieci")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)


class FakeClock:
    """Zegar sterowany przez test; `sleep` przesuwa czas zamiast czekać."""

    def __init__(self, start_wall: float = 1_700_000_000.0, start_mono: float = 1000.0) -> None:
        self._wall = start_wall
        self._mono = start_mono
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self._mono

    def wall(self) -> float:
        return self._wall

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self._mono += seconds
        self._wall += seconds

    def jump_wall(self, seconds: float) -> None:
        """Skok zegara ściennego bez zmiany monotonicznego (NTP / zmiana czasu)."""
        self._wall += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def profile() -> ApiProfile:
    return ApiProfile(base_url="https://test-dane.biznes.gov.pl/api/ceidg/v3")


def list_record(idx: int = 1, **overrides: Any) -> dict[str, Any]:
    """Element `firmy[]` w kształcie z dokumentacji API (s. 5-6)."""
    rec: dict[str, Any] = {
        "id": f"9D2531B1-6DED-4538-95EA-22FF2C7D2E{idx:02d}",
        "nazwa": "Adam IntegracjaMGMF",
        "adresDzialalnosci": {
            "ulica": "ul. Zwierzyniecka",
            "budynek": "1",
            "miasto": "Białystok",
            "wojewodztwo": "PODLASKIE",
            "powiat": "Białystok",
            "gmina": "Białystok",
            "kraj": "PL",
            "kod": "15-333",
            "terc": "2061011",
            "simc": "0922410",
            "ulic": "26305",
        },
        "wlasciciel": {
            "imie": "Adam",
            "nazwisko": "IntegracjaMGMF",
            "nip": "3563457932",
            "regon": "618155359",
        },
        "dataRozpoczecia": "2014-07-29",
        "status": "WYKRESLONY",
        "link": f"https://test-dane.biznes.gov.pl/api/ceidg/v3/firma/9D2531B1-{idx:02d}",
    }
    rec.update(overrides)
    return rec


def detail_record(idx: int = 1, **overrides: Any) -> dict[str, Any]:
    """Element `firma[]` w kształcie z dokumentacji API (s. 11-12): 4 PKD, spółka, adres kor."""
    base = list_record(idx)
    rec: dict[str, Any] = {
        **base,
        "nazwa": "Adam IntegracjaMGMF - Zmiana",
        "adresKorespondencyjny": dict(base["adresDzialalnosci"]),
        "obywatelstwa": [{"symbol": "PL", "kraj": "Polska"}],
        # 2025, bo tyle zwraca rejestr. Ta atrapa niosła „2007" i była **jedyną** podstawą
        # wyboru rocznika słownika PKD w fazie 4 — wszystkie prawdziwe odpowiedzi API
        # (11 wystąpień w fixtures i próbkach produkcyjnych) mówią 2025. Atrapa, która
        # zaprzecza zmierzonemu zachowaniu, jest gorsza niż jej brak: wygląda jak dowód.
        "rokPkd": "2025",
        # Kody **istniejące w PKD 2025**, bo tyle deklaruje `rokPkd` wyżej. Wcześniej stały tu
        # `3030Z` i `6201Z` z PKD 2007 — a `6201Z` w 2025 nie istnieje (programowanie przeniesiono
        # na 62.10), `3030Z` rozdzielono na `3031Z`/`3032Z`. Atrapa niosąca kody nieistniejące
        # w roczniku, który sama deklaruje, jest tym rodzajem fikcji, przez który wybraliśmy
        # w fazie 4 zły słownik.
        "pkd": [
            {"kod": "3031Z", "nazwa": "Produkcja cywilnych statków powietrznych"},
            {"kod": "6210B", "nazwa": "Pozostała działalność w zakresie programowania"},
            {"kod": "0111Z", "nazwa": "Uprawa zbóż"},
            {"kod": "4711Z", "nazwa": "Sprzedaż detaliczna"},
        ],
        "pkdGlowny": {"kod": "3031Z", "nazwa": "Produkcja cywilnych statków powietrznych"},
        "spolki": [{"nip": "8567773578", "regon": "113110043"}],
        "status": "WYLACZNIE_W_FORMIE_SPOLKI",
        "numerStatusu": 9,
        "wspolnoscMajatkowa": 0,
        "email": "adam@example.test",
    }
    rec.update(overrides)
    return rec
