"""Port do rejestru i jedyny adapter, jaki etap 1 posiada.

Port istnieje od pierwszego dnia, mimo że ma jedną implementację, bo to jest ubezpieczenie
zapisane w `ceidg-tool/docs/adr/0023`, decyzja 9: gdy stanowisko ministerstwa pozwoli sięgnąć
po API, dojdzie drugi adapter i linia w konfiguracji, a nie przebudowa. Gdy nie pozwoli —
produkt działa dalej.

`Zrodlo` ma **dokładnie jeden** człon i pilnuje tego test. Komunikat tego testu nazywa
adapter, który dokłada drugi, żeby nikt nie dołożył go mimochodem.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from ..errors import OdpisNieczytelnyError
from ..identity import NumerKRS
from .czytanie import wczytaj_odpis
from .model import Odpis


class Zrodlo(Enum):
    """Skąd wziął się odpis. Etap 1 zna jedną odpowiedź."""

    PLIK_OPERATORA = "plik_operatora"


class RejestrKRS(Protocol):
    """Dostawca odpisu."""

    def pobierz(self, numer: NumerKRS | None) -> Odpis: ...


class OdpisZPliku:
    """Odpis zapisany wcześniej przez operatora do pliku.

    Jedyny adapter etapu 1. Nie otwiera gniazda i nie wie, co to adres — dostaje ścieżkę.
    """

    zrodlo = Zrodlo.PLIK_OPERATORA

    def __init__(self, sciezka: Path) -> None:
        self._sciezka = sciezka
        self._tresc: str | None = None

    @property
    def tresc(self) -> str:
        """Bajty, z których powstał odpis — dokładnie te, nie odczytane drugi raz.

        Dziennik zapisuje ładunek i skrót materiału. Drugi odczyt tego samego pliku dawałby
        to, co leży na dysku w chwili zapisu dziennika, a nie to, z czego powstały werdykty —
        i gdyby plik zmienił się w międzyczasie, odtworzenie powiedziałoby „materiał się nie
        zmienił" o materiale, którego nigdy nie oceniało. To jest jedyne twierdzenie, jakie
        dziennik stawia o swoim ładunku, więc ma wynikać z konstrukcji.
        """
        if self._tresc is None:  # pragma: no cover - wołane po `pobierz`
            raise OdpisNieczytelnyError("Odpis nie został jeszcze odczytany")
        return self._tresc

    def pobierz(self, numer: NumerKRS | None = None) -> Odpis:
        return wczytaj_odpis(self._wczytaj(), numer=numer)

    def _wczytaj(self) -> Mapping[str, Any]:
        try:
            tresc = self._sciezka.read_text(encoding="utf-8")
        except OSError as blad:
            raise OdpisNieczytelnyError(
                f"Nie da się odczytać pliku {self._sciezka}: {blad}"
            ) from blad
        try:
            dane = json.loads(tresc)
        except json.JSONDecodeError as blad:
            raise OdpisNieczytelnyError(
                f"Plik {self._sciezka} nie jest poprawnym JSON-em: {blad}"
            ) from blad
        if not isinstance(dane, Mapping):
            raise OdpisNieczytelnyError(f"Plik {self._sciezka} nie zawiera obiektu JSON")
        self._tresc = tresc
        return dane
