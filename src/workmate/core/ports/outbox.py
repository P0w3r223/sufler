"""Port SKRZYNKI NADAWCZEJ rozmowy — pliki, które agent zostawił do dostarczenia (ADR 0009 paczki).

Do tej pory dostawa pliku była AKTEM: model wołał ``reply_with_file``, podając treść i format,
a aplikacja renderowała ją do bajtów. Ten port dokłada drugą drogę — model tworzy plik powłoką
(narzędzie ``Bash``, ADR 0057) w podkatalogu ``outputs/`` swojego katalogu roboczego, a drzwi
zabierają go po zakończeniu tury. Dzięki temu narzędzia i renderery zostają po stronie tego, co
model UMIE zrobić sam: wynik pracy powstaje tam, gdzie ta praca się toczy.

Semantyka jest SKRZYNKĄ NADAWCZĄ, nie katalogiem: co leży w środku po turze, zostaje wysłane,
a po udanej wysyłce znika. To upraszcza dwie rzeczy naraz — nie trzeba porównywać czasów
modyfikacji (zegary dwóch kontenerów), a nieudana wysyłka zostawia plik na miejscu, więc
kolejna tura ponowi. Cena: plik zapisany jako etap pośredni wyjedzie do rozmówcy, dlatego
opis narzędzia ``Bash`` musi mówić o ``outputs/`` wprost.

``Protocol`` jak pozostałe porty. Metody są SYNCHRONICZNE — biegną w tej samej puli wątków,
w której dispatch drzwi woła runtime agenta.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Deliverable:
    """Plik gotowy do wysłania: bezpieczna nazwa, bajty i typ MIME.

    ``name`` jest już przepuszczona przez białą listę rozszerzeń i slug (nazwę nadał MODEL,
    tworząc plik powłoką), więc konsument może jej użyć wprost jako nazwy załącznika.
    """

    name: str
    content: bytes
    content_type: str


class OutboxRepository(Protocol):
    """Odczyt i sprzątanie skrzynki nadawczej rozmowy. Ścieżki liczone od korzenia workspace."""

    def collect(self, dirpath: str) -> list[Deliverable]:
        """Zwróć pliki leżące w skrzynce ``dirpath``; pusta lista, gdy katalogu nie ma.

        Brak katalogu to normalny stan — większość tur nic nie dostarcza — więc NIE jest błędem.
        Implementacja odrzuca wszystko, co nie jest zwykłym plikiem bezpośrednio w skrzynce
        (podkatalogi, dowiązania, urządzenia): skrzynka jest płaska, a dowiązanie pozwoliłoby
        wyprowadzić na zewnątrz treść, do której model ma tylko odczyt.
        """
        ...

    def discard(self, dirpath: str, name: str) -> None:
        """Usuń dostarczony plik ze skrzynki. Brak pliku nie jest błędem (idempotencja)."""
        ...
