"""Anonimizacja odpisu przed wpuszczeniem go do repozytorium. Moduł czysty.

**Logika mieszka w pakiecie, nie w skrypcie, i to jest decyzja.** Tu powstaje materiał
dowodowy — plik, na który potem powołuje się `tests/fixtures/odpis_traits.yaml`. Kod, który
wytwarza dowód, ma podlegać najostrzejszym regułom projektu, a nie stać obok nich.

**Pisany od nowa, nie przeniesiony.** Odpowiednik z `ceidg-tool` podnosił identyfikatory do
wielkich liter, przez co suita offline utrwalała brak dokładnie tej własności, która wywróciła
produkcję. Tutaj obowiązuje reguła odwrotna: zmieniamy **wartości osobowe i identyfikujące**,
kształtu nie ruszamy.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Własności, których anonimizator nie ma prawa naruszyć. To są KODY, nie proza: każdemu
# odpowiada sprawdzenie w `tests/test_anonimizator.py`, a test porównuje oba zbiory w obie
# strony. Dopisanie własności bez testu zapala bramkę — lista, której nikt nie egzekwuje,
# jest komentarzem.
#
#   oba_zapisy_okresu     — kropkowy i słowny wracają bez zmian
#   format_daty_zlozenia  — data złożenia wraca bez zmian
#   obecnosc_kluczy       — nie dopisujemy klucza, którego nie było, i nie usuwamy istniejącego
#   pustka_dzialow        — pustka zostaje pustką, brak zostaje brakiem
#   wielkosc_liter        — wartości spoza pól osobowych wracają znak w znak
WLASNOSCI_ZACHOWYWANE = (
    "oba_zapisy_okresu",
    "format_daty_zlozenia",
    "obecnosc_kluczy",
    "pustka_dzialow",
    "wielkosc_liter",
)

ZASTEPCZY_NUMER = "0000000001"
ZASTEPCZA_NAZWA = "SPOLKA ANONIMOWA SPOLKA Z OGRANICZONA ODPOWIEDZIALNOSCIA"
ZASTEPCZY_NIP = "0000000000"
ZASTEPCZY_REGON = "000000000"
ZASTEPCZE_IMIE = "IMIE"
ZASTEPCZE_NAZWISKO = "NAZWISKO"
ZASTEPCZY_PESEL = "00000000000"

_PODMIANY = {
    "imiona": ZASTEPCZE_IMIE,
    "imie": ZASTEPCZE_IMIE,
    "nazwisko": ZASTEPCZE_NAZWISKO,
    "nazwiskoDwuczlonowe": ZASTEPCZE_NAZWISKO,
    "pesel": ZASTEPCZY_PESEL,
    "nazwa": ZASTEPCZA_NAZWA,
    "numerKRS": ZASTEPCZY_NUMER,
    "nip": ZASTEPCZY_NIP,
    "regon": ZASTEPCZY_REGON,
}


def anonimizuj(wezel: Any, klucz: str = "") -> Any:
    """Zwraca kopię z podmienionymi wartościami osobowymi i identyfikującymi.

    Struktura nie jest dotykana: klucze, ich kolejność, obecność i typy zostają. Wartości
    spoza mapy podmian wracają identyczne — w szczególności **każdy napis okresu i każda data**.
    """
    if isinstance(wezel, Mapping):
        return {k: anonimizuj(v, k) for k, v in wezel.items()}
    if isinstance(wezel, list):
        return [anonimizuj(element, klucz) for element in wezel]
    if klucz in _PODMIANY and isinstance(wezel, str):
        return _PODMIANY[klucz]
    return wezel
