"""Asystent językowy fazy 4 — czwarty producent `Criteria` (ADR-0011).

Asystent nie jest nowym potokiem. Stoi **przed** sekwencją decyzyjną z `ui/flow.py`, produkuje
`Criteria` i kończy — obok flag CLI i pytań kreatora. Z tego jednego umiejscowienia
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
from typing import Literal, Protocol

from ..criteria import Criteria
from .schema import AssistantAnswer, OgraniczenieKod

__all__ = [
    "Assistant",
    "AssistantAnswer",
    "AssistantResult",
    "BrakAsystenta",
    "OgraniczenieKod",
    "PowodBrakuAsystenta",
]


Asystent = Literal["buduj", "nieproszony", "wylaczony"]
"""Co wywołujący chce zrobić z asystentem — **trzy** stany, nie dwa booleany.

Dwie flagi (`asystent=`, `wylaczony=`) opisywały trzy stany czwórką kombinacji, a czwarta
— „buduj i jednocześnie wyłączony" — nie znaczyła nic i nikt jej nie bronił. Jeden typ czyni
ją niewyrażalną, co jest tańsze niż sprawdzenie w ciele funkcji: sprawdzenie trzeba znaleźć,
a typu nie da się ominąć.

Nazwy są dosłowne, bo rozróżnienie jest nośne dla ekranu. `nieproszony` to „ta ścieżka nie ma
jak go użyć" i ekran o tym **milczy** — nie ma czego tłumaczyć. `wylaczony` to decyzja
operatora (`--bez-asystenta`), którą pierwszy ekran ma pokazać, a sprostowanie o nieudanej
budowie ma przy niej milczeć: nikt nie potrzebuje ostrzeżenia o tym, o co sam poprosił.
"""

PowodBrakuAsystenta = Literal[
    "BRAK_KLUCZA",
    "BRAK_PAKIETU",
    "BRAK_SLOWNIKA",
    "WYLACZONY_FLAGA",
]
"""Zamknięty zbiór powodów, dla których asystenta nie ma. Żaden nie jest błędem (ADR-0011).

`Literal`, a nie zwykły napis, z tego samego powodu co `PowodBrakuRaportu`: piąty powód dopisany
w `pipeline` bez zdania w `texts` daje wtedy **błąd mypy przy zwrocie**, a nie `KeyError` w środku
kreatora — a `KeyError` nie należy do taksonomii `CeidgError`, więc wyszedłby śladem stosu.

Mieszka tutaj, a nie w `ui/texts.py`, choć tam stoi wzorzec. Producentem `PowodBrakuRaportu` jest
`flow`, czyli moduł z tej samej warstwy co `texts`; producentem tego jest `pipeline`, warstwa
**niżej** — a `pipeline` importujący z `ui/` odwracałby kierunek, którego pilnuje reguła granic 8.
Ten moduł jest czysty, `pipeline` już go importuje i daje się go wczytać bez zainstalowanego SDK,
więc zdania zostają w `texts`, a kody tutaj."""


@dataclass(frozen=True)
class BrakAsystenta:
    """Powód nieobecności asystenta razem ze szczegółem od tego, kto ją wykrył.

    Jeden obiekt, a nie dwa pola w `Deps`, bo dwa pola dopuszczają czwarty stan — *powód
    ustawiony, asystent obecny* — którego mypy nie umie wykluczyć i który trzeba by pilnować
    osobnym testem. Przy jednym obiekcie `(assistant is None) == (brak is not None)` jest
    kształtem, który mypy zawęża sam.

    `szczegol` niesie **cudze zdanie**, nie nasze: `load_pkd` potrafi powiedzieć „brak słownika
    i oto polecenie, które go zbuduje", a spłaszczenie tego do zdania o kluczu i pakiecie było
    defektem z przebiegu B6 — wskazywaniem palcem na dwie rzeczy, które akurat działały.
    """

    powod: PowodBrakuAsystenta
    szczegol: str | None = None


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
