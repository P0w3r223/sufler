"""Dowód zgody na odczyt prywatnej historii promptów — typ, którego nie da się zbudować obok bramki.

Bramka zgody stała wcześniej w orkiestratorze (``app.run``), a adapter czytający ``~/.claude``
przyjmował wywołanie od każdego. Teraz dowód jest ARGUMENTEM odczytu: ``ConsentProof`` powstaje
wyłącznie w ``grant_consent`` (konstruktor odmawia bez wewnętrznego znacznika), a adapter bez
dowodu nie ma jak zacząć czytać. Zgoda przestaje być konwencją wołającego, a staje się typem.

ZAKRES GWARANCJI: to zabezpieczenie przed POMYŁKĄ, nie przed przeciwnikiem. W Pythonie da się
obejść konstruktor (``object.__new__``, ``copy.deepcopy``, import ``_GATE_TOKEN``) — kto to robi,
robi to świadomie. Wartość polega na tym, że zwykłe wywołanie odczytu bez przejścia przez bramkę
nie kompiluje się u ``mypy`` i pada w czasie działania.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Znacznik wewnętrzny: jedyna wartość, którą ``ConsentProof`` przyjmuje. Wymusza przejście przez
# ``grant_consent`` — obiekt nie powstaje „przy okazji" ani z przypadkowego ``ConsentProof()``.
_GATE_TOKEN = object()


class ConsentError(RuntimeError):
    """Próba zbudowania dowodu zgody z pominięciem bramki."""


@dataclass(frozen=True)
class ConsentProof:
    """Dowód, że użytkownik wyraził zgodę na odczyt swojej historii promptów.

    Domyka pomyłkę, nie przeciwnika — patrz „ZAKRES GWARANCJI" w nagłówku modułu.
    """

    _token: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._token is not _GATE_TOKEN:
            raise ConsentError(
                "ConsentProof powstaje wyłącznie przez grant_consent() — bramka zgody "
                "jest warunkiem odczytu prywatnej historii promptów."
            )


def grant_consent(*, flag: bool, env_consent: bool) -> ConsentProof | None:
    """Zwróć dowód zgody albo ``None``, gdy nie dała jej ani flaga ``--consent``, ani env."""
    if not (flag or env_consent):
        return None
    return ConsentProof(_GATE_TOKEN)


def require_consent(proof: object) -> None:
    """Twardy strażnik na granicy odczytu — odmawia, gdy dowód nie jest prawdziwym ``ConsentProof``.

    Sam typ pilnuje tego już statycznie (``mypy``); to jest zabezpieczenie dla wołających bez
    typów, żeby ``None`` albo ``True`` nie przeszło jako „zgoda".
    """
    if not isinstance(proof, ConsentProof):
        raise ConsentError(
            "Odczyt historii promptów wymaga dowodu zgody (ConsentProof z grant_consent())."
        )
