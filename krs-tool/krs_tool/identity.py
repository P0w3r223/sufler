"""Jedyny producent `NumerKRS` (reguła granic 8, niesiona przez mypy strict).

Granica zakresu tego narzędzia jest tutaj, a nie w dokumentacji: skoro `NumerKRS` powstaje
wyłącznie w tym module, a ten moduł przyjmuje tylko dziesięciocyfrowy numer rejestrowy, to
**żadna jednoosobowa działalność nie ma jak wejść do potoku**. Granica prawna i granica kodu
to ta sama linia (`docs/adr/0001`).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import NewType

from .errors import OdpisNieczytelnyError

NumerKRS = NewType("NumerKRS", str)

DLUGOSC_NUMERU = 10
_TYLKO_CYFRY = re.compile(r"\D")

# Wagi sumy kontrolnej NIP. KRS własnej sumy nie ma, więc to jedyny sposób, żeby odróżnić
# te dwa dziesięciocyfrowe numery od siebie — i odróżnia je tylko z pewnym prawdopodobieństwem.
_WAGI_NIP = (6, 5, 7, 2, 3, 4, 5, 6, 7)


class KsztaltNumeru(Enum):
    """Co da się powiedzieć o dziesięciu cyfrach, zanim ktokolwiek je sprawdzi w rejestrze."""

    POPRAWNY = "poprawny"
    WYGLADA_NA_NIP = "wyglada_na_nip"
    ZLA_DLUGOSC = "zla_dlugosc"


def _cyfry(surowy: str) -> str:
    return _TYLKO_CYFRY.sub("", surowy)


def wyglada_na_nip(surowy: str) -> bool:
    """Czy dziesięć cyfr spełnia sumę kontrolną NIP.

    Losowy numer KRS spełnia ją z prawdopodobieństwem około 1/11, więc to jest **przesłanka,
    nie rozstrzygnięcie**. Dlatego `ocen_ksztalt` zwraca ostrzeżenie, a nie odmowę: narzędzie
    ma powiedzieć „to wygląda na NIP", a nie odesłać operatora z niczym (reguła braku ślepych
    zaułków, przeniesiona z ADR-0017 tamtego pod-projektu).
    """
    cyfry = _cyfry(surowy)
    if len(cyfry) != DLUGOSC_NUMERU:
        return False
    # Wag jest dziewięć, cyfr dziesięć: ostatnia cyfra to suma kontrolna, nie składnik.
    # `strict=True` zostaje, bo to on wyłapał pierwszą wersję tego wyrażenia.
    suma = sum(waga * int(cyfra) for waga, cyfra in zip(_WAGI_NIP, cyfry[:-1], strict=True))
    kontrolna = suma % 11
    return kontrolna != 10 and kontrolna == int(cyfry[-1])


def ocen_ksztalt(surowy: str) -> KsztaltNumeru:
    """Ocena wejścia przed jakimkolwiek użyciem — bez podejmowania decyzji za operatora."""
    cyfry = _cyfry(surowy)
    if len(cyfry) != DLUGOSC_NUMERU:
        return KsztaltNumeru.ZLA_DLUGOSC
    if wyglada_na_nip(cyfry):
        return KsztaltNumeru.WYGLADA_NA_NIP
    return KsztaltNumeru.POPRAWNY


def numer_krs(surowy: str) -> NumerKRS:
    """Postać kanoniczna numeru KRS: dziesięć cyfr, wiodące zera zachowane.

    Zera wiodące są znaczące — `0000028860` to nie `28860`. Numer trzymamy więc jako napis,
    nigdy jako liczbę.
    """
    cyfry = _cyfry(surowy)
    if len(cyfry) != DLUGOSC_NUMERU:
        raise OdpisNieczytelnyError(
            f"Numer KRS ma mieć {DLUGOSC_NUMERU} cyfr, a otrzymano {len(cyfry)}: {surowy!r}"
        )
    return NumerKRS(cyfry)
