"""Asystent językowy fazy 4 — czwarty producent `Criteria` (ADR-0011).

Asystent nie jest nowym potokiem. Stoi **przed** sekwencją decyzyjną z `ui/flow.py`, produkuje
`Criteria` i kończy — obok flag CLI, pliku YAML i pytań kreatora. Z tego jednego umiejscowienia
wynika reszta: liczba żądań `count`, tabela kosztów, ścieżka zgody na produkcję i próg podziału
na partie zostają nietknięte, bo asystent kończy pracę, zanim którykolwiek z nich się zacznie —
runda dopytania z ADR-0017 też, bo toczy się w całości nad modelem. Zdanie mówiło tu „dokładnie
jedno żądanie `count`" i nie było prawdą już od ADR-0012; liczbę opisuje `ui/flow.py`.

Reguła granic 13: `assistant/*` nie importuje `client`, `store` ani `pipeline`. To jest
strukturalna postać zdania z §B — „do modelu trafia treść pytania i słownik PKD; pobrane rekordy
nigdy". Rekordy nie mogą tędy przejść, bo nie ma krawędzi w grafie importów, którą mogłyby iść.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from ..criteria import Criteria
from .schema import AssistantAnswer, OgraniczenieKod

__all__ = [
    "Assistant",
    "AssistantAnswer",
    "AssistantResult",
    "OgraniczenieKod",
]


@dataclass(frozen=True)
class AssistantResult:
    """Wynik interpretacji: gotowe kryteria plus to, co ekran ma o nich powiedzieć.

    `kody_pkd` niesie **kod i nazwę ze słownika lokalnego**, nigdy nazwę od modelu. To jest ta
    różnica, która pozwala operatorowi cokolwiek sprawdzić: zły kod jest niewidoczny, zła nazwa
    branży rzuca się w oczy.
    """

    kryteria: Criteria
    kody_pkd: tuple[tuple[str, str], ...] = ()
    ograniczenia: tuple[OgraniczenieKod, ...] = ()
    # Runda dopytania (ADR-0017): pytanie i gotowe zdania do wyboru, gdy z opisu nie dało się
    # zbudować żadnego filtra. Puste w zdecydowanej większości interpretacji.
    pytanie: str = ""
    propozycje: tuple[str, ...] = ()


class Assistant(Protocol):
    """Tłumacz zdania na kryteria. Implementacja sieciowa w `caller.py`, atrapa w testach."""

    def interpret(self, opis: str, *, dzisiaj: date) -> AssistantResult: ...
