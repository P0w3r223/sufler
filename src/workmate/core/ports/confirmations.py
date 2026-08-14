"""Port rejestru zapowiedzianych mutacji — punkt kontrolny człowieka (ADR 0065).

Werdykt ``confirm`` znaczy „nie rób tego, dopóki człowiek tego nie potwierdzi". Problem w tym,
że bot rozmawia z człowiekiem wyłącznie przez model, więc każde „użytkownik się zgodził" jest
relacją modelu — a model w tej turze mógł już czytać treść, która go do tego namówiła.

Mechanizm, który da się tu uczciwie zbudować: mutacja z werdyktem ``confirm`` zostaje
ZAPOWIEDZIANA i odrzucona, a przechodzi dopiero wtedy, gdy TA SAMA prośba (ten sam człowiek, ta
sama notatka, ta sama treść) wróci w PÓŹNIEJSZEJ turze. Tura powstaje tylko wtedy, gdy ktoś
napisał, więc powtórzenie dowodzi, że człowiek odezwał się po zobaczeniu, co miałoby się zmienić.

Czego to NIE dowodzi — i trzeba to nazwać, żeby nikt nie zbudował na tym więcej, niż uniesie:
że człowiek się ZGODZIŁ. Dowodzi, że napisał cokolwiek. Mocniejszy dowód wymagałby kanału poza
modelem (przycisk, osobna komenda), którego ta instalacja nie ma. Zapowiedź zawęża więc okno,
zamiast je zamykać: kasowanie wymaga dwóch tur i przechodzi przez oczy człowieka po drodze.
"""

from __future__ import annotations

from typing import Protocol


class ConfirmationLedger(Protocol):
    """Pamięć mutacji zapowiedzianych i czekających na powrót tej samej prośby."""

    def seen(self, key: str) -> bool:
        """Czy ta dokładnie prośba została już zapowiedziana (i nie wygasła)?"""
        ...

    def remember(self, key: str) -> None:
        """Zapamiętaj zapowiedzianą prośbę; wpis ma wygasać, a nie czekać w nieskończoność."""
        ...

    def forget(self, key: str) -> None:
        """Skasuj wpis po wykonaniu — zgoda jest jednorazowa, nie staje się stałym pozwoleniem."""
        ...
