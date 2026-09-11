"""Model odczytu odpisu. Moduł czysty — bez wejścia/wyjścia i bez zegara.

Dwie decyzje widoczne w typach:

**Okres zawsze niesie swój surowy zapis.** Rejestr zapisuje ten sam okres na co najmniej dwa
sposoby („OD 01.01.2023 DO 31.12.2023" obok „OD 1 STYCZNIA 2000 ROKU DO 31 GRUDNIA 2000
ROKU"), a wpisu, którego nie umiemy odczytać, **nie wolno zgadywać**. `okres=None` przy
zachowanym `zapis_okresu` to stan poprawny i wypisywalny, a nie brak danych.

**Pustka działu jest wartością, nie brakiem.** U zdrowej spółki działy 4 i 5 wracają puste,
więc niepustość jest binarną flagą ryzyka. Gdyby model mylił „dział pusty" z „działu nie
było w pliku", sygnał najwyższego poziomu opierałby się na dwuznaczności.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import NewType

from ..identity import NumerKRS

# Reguła granic 9, część pierwsza: dzień bilansowy powstaje wyłącznie w `czytanie.py`.
# Terminy ustawowe liczy się od dnia bilansowego, nigdy od 31 grudnia — rok obrotowy nie musi
# być kalendarzowy, a po zmianie bywa dłuższy niż dwanaście miesięcy.
DzienBilansowy = NewType("DzienBilansowy", date)

# Klucz wzmianki o rocznym sprawozdaniu finansowym — jedyny rodzaj wzmianki, na który patrzy
# dziś warstwa sygnałów. Stoi tutaj, a nie w dwóch miejscach naraz: nazwa pochodzi od rejestru,
# więc należy do słownika warstwy odczytu, a `texts.py` i `signals/` biorą ją stąd.
RODZAJ_SPRAWOZDANIE_FINANSOWE = "wzmiankaOZlozeniuRocznegoSprawozdaniaFinansowego"

# Wartość pola `naglowekA.rejestr` oznaczająca rejestr przedsiębiorców. **Założenie, nie
# pomiar** — `docs/pomiary.md`, wiersz 10. Kierunek pomyłki jest bezpieczny: nieznana wartość
# wyklucza regułę braku sprawozdania, zamiast ją zapalać.
REJESTR_PRZEDSIEBIORCOW = "P"


@dataclass(frozen=True)
class Okres:
    """Okres sprawozdawczy odczytany ze wzmianki."""

    od: date
    do: DzienBilansowy


@dataclass(frozen=True)
class Wzmianka:
    """Jedna wzmianka o złożonym dokumencie.

    `rodzaj` to klucz rejestru (np. `wzmiankaOZlozeniuRocznegoSprawozdaniaFinansowego`).
    `data_zlozenia` to **data złożenia, nie zatwierdzenia** — rejestr tej drugiej nie
    publikuje, i to jest powód, dla którego zarzut spóźnienia jest z danych publicznych
    nieudowadnialny (`docs/niezmierzone.md`, wiersz 3).
    """

    rodzaj: str
    data_zlozenia: date
    zapis_okresu: str
    okres: Okres | None


@dataclass(frozen=True)
class Dzial:
    """Zawartość działu sprowadzona do tego, co dziś potrafimy o niej powiedzieć."""

    numer: int
    obecny: bool
    pusty: bool


@dataclass(frozen=True)
class Odpis:
    """Odpis sprowadzony do modelu odczytu."""

    numer: NumerKRS
    rejestr: str
    stan_z_dnia: date
    data_ostatniego_wpisu: date | None
    nazwa: str
    forma_prawna: str
    nip: str | None
    regon: str | None
    dzien_konczacy_rok_obrotowy: str | None
    wzmianki: tuple[Wzmianka, ...]
    dzialy: tuple[Dzial, ...]
    syntetyczny: bool

    def dzial(self, numer: int) -> Dzial | None:
        """Dział o danym numerze albo `None`, gdy w pliku go nie było."""
        for pozycja in self.dzialy:
            if pozycja.numer == numer:
                return pozycja
        return None

    def wzmianki_nieczytelne(self) -> tuple[Wzmianka, ...]:
        """Wzmianki, których okresu nie udało się odczytać — do zgłoszenia, nie do ukrycia."""
        return tuple(w for w in self.wzmianki if w.okres is None)
