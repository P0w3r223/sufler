"""Port SKRZYNKI NADAWCZEJ rozmowy — pliki, które agent zostawił do dostarczenia.

Decyzja: ADR 0009 paczki wdrożeniowej `infra-docker-workmate`
(`docs/decyzje/0009-konsolidacja-powierzchni-narzedziowej.md`) — numer 0009 w TYM repozytorium
znaczy co innego, więc odwołania podajemy pełną nazwą.

Do tej pory dostawa pliku była AKTEM: model wołał ``reply_with_file``, podając treść i format,
a aplikacja renderowała ją do bajtów. Ten port dokłada drugą drogę — model tworzy plik powłoką
(narzędzie ``Bash``, ADR 0057) w podkatalogu ``outputs/`` swojego katalogu roboczego, a drzwi
zabierają go po zakończeniu tury.

Semantyka jest SKRZYNKĄ NADAWCZĄ, nie katalogiem: co leży w środku po turze, zostaje wysłane,
a po udanej wysyłce znika. Cena: plik zapisany jako etap pośredni wyjedzie do rozmówcy, dlatego
opis narzędzia ``Bash`` mówi o ``outputs/`` wprost.

**Wypis jest ODDZIELONY od odczytu i to nie jest podział estetyczny.** Zawartość skrzynki
dyktuje model mający powłokę, więc wczytanie wszystkiego do pamięci przed sprawdzeniem limitów
dawałoby mu sposób na wywrócenie procesu drzwi — a ten obsługuje WSZYSTKIE kanały, nie tylko tę
rozmowę. ``list_entries`` zwraca metadane (nazwa, rozmiar), rdzeń na nich rozstrzyga, a ``read``
dotyka dysku wyłącznie dla pozycji, które przeszły.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sufler.core.errors import SuflerError


class PermanentDeliveryError(SuflerError):
    """Wysyłka odrzucona TRWALE — ponowienie da ten sam wynik.

    Rozróżnienie jest nośne, nie opisowe: pozycja trwale odrzucona jest sprzątana ze skrzynki,
    a przejściowa zostaje do ponowienia. Bez tej klasy każde 4xx z Graph (nazwa nieakceptowana
    przez SharePoint, zniknięty root wątku) zostawiałoby plik na zawsze, a każda kolejna tura
    w tej rozmowie płaciłaby dwa żądania i doklejała „spróbuję ponownie" — czyli dokładnie
    zatrutą wiadomość, której cała reguła sprzątania ma unikać.
    """


class OutboxReadError(SuflerError):
    """Pozycji NIE DA SIĘ przeczytać, choć plik w skrzynce nadal jest.

    Osobne od ``None`` z ``read`` i to rozróżnienie jest całą treścią tej klasy. ``None`` znaczy
    „pliku już nie ma" — wolno je przemilczeć, bo nie ma czego wysyłać ani sprzątać. Odmowa
    dostępu, błąd I/O albo urośnięcie ponad sufit odczytu to stan PRZECIWNY: plik zostaje na
    wolumenie. Przemilczany wypadał ze zbioru pozycji zatrzymanych (``_ours``), więc następna
    tura widziała go jako podłożony z innej rozmowy i kasowała — praca modelu znikała bez
    jednego zdania w odpowiedzi.

    ``permanent`` rozstrzyga sprzątanie tak samo jak przy wysyłce (``PermanentDeliveryError``):
    trwałe → pozycja znika z podaniem powodu, przejściowe → zostaje do ponowienia.
    """

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


@dataclass(frozen=True)
class OutboxEntry:
    """Metadane pozycji w skrzynce — tyle, ile trzeba, żeby rdzeń rozstrzygnął bez czytania."""

    name: str
    size: int


@dataclass(frozen=True)
class Deliverable:
    """Plik gotowy do wysłania: nazwa, bajty i typ MIME.

    ``name`` pochodzi z dysku, więc nadał ją MODEL. Normalizuje ją rdzeń przed wysyłką
    (``safe_filename``) — konsument dostaje już nazwę z białej listy rozszerzeń i sluga.
    """

    name: str
    content: bytes
    content_type: str


class OutboxRepository(Protocol):
    """Wypis, odczyt i sprzątanie skrzynki rozmowy. Ścieżki liczone od korzenia workspace."""

    def list_entries(self, dirpath: str) -> list[OutboxEntry]:
        """Zwróć metadane pozycji w skrzynce ``dirpath``; pusta lista, gdy skrzynki nie ma.

        Brak katalogu to normalny stan — większość tur nic nie dostarcza — więc NIE jest błędem.
        Implementacja pomija wszystko, co nie jest zwykłym plikiem bezpośrednio w skrzynce,
        i odrzuca skrzynkę, która sama jest dowiązaniem: inaczej ``outputs`` wskazujące katalog
        innej rozmowy oddawałoby jej pliki do wysyłki i do skasowania.
        """
        ...

    def read(self, dirpath: str, name: str) -> Deliverable | None:
        """Wczytaj pozycję do wysyłki; ``None``, gdy zniknęła między wypisem a odczytem.

        ``None`` zarezerwowane jest dla ZNIKNIĘCIA i tylko dla niego. Pozycja, która jest,
        ale nie daje się przeczytać, idzie ``OutboxReadError`` — patrz tam po powód.
        """
        ...

    def discard(self, dirpath: str, name: str) -> None:
        """Usuń pozycję ze skrzynki. Brak pliku nie jest błędem (idempotencja).

        NIE PODNOSI — także wtedy, gdy usunięcie się nie uda (odmowa dostępu do katalogu,
        uchwyt na pliku). Rdzeń woła to w środku pętli dostawy, po zdjęciu migawki startowej
        i przed aktualizacją stanu ponawiania, więc wyjątek stąd zostawiałby rozmowę
        z rozjechanym stanem, a jedyną szkodą z nieudanego sprzątania jest pozycja, która
        w następnej turze i tak zostanie rozpoznana jako obca i nie pojedzie drugi raz.
        """
        ...
