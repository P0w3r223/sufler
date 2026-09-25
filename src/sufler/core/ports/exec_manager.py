"""Porty menedżera wykonawców per rozmowa (ADR infra 0012 — domknięcie izolacji rozmów).

Dwie granice, dwa kontrakty:

- ``ExecManager`` widzi APLIKACJA (drzwi Teams). Zna JEDEN czasownik — ``ensure(scope)`` — bo tyle
  jej wystarcza: „daj mi gniazdo wykonawcy tej rozmowy". Cykl życia (reap, limit, reconcile) należy
  do menedżera, nie do aplikacji, więc nie wycieka do tego portu.
- ``ContainerEngine`` widzi MENEDŻER. To wąski kontrakt nad silnikiem kontenerów (Docker): postaw
  wykonawcę ze STAŁEGO szablonu, usuń go, wypisz żywe zarządzane. `docker.sock` jest równoważnikiem
  roota na hoście (ADR 0012), więc menedżer dotyka go wyłącznie przez te trzy czasowniki, a nie
  przez powłokę — jedyną zmienną wejściową jest zwalidowany ``scope``, wstawiany do żądania Docker
  API, nie do wiersza poleceń.

Rdzeń zna te kontrakty, ale nie ich implementację: produkcja rozmawia z Dockerem po gnieździe, testy
podstawiają atrapy. Dzięki temu logika cyklu życia (``ExecManagerService``) jest czysta i testowalna
bez demona Dockera.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ContainerSpec:
    """Statyczny szablon ``docker run`` wykonawcy jednego scope'a — składa go MENEDŻER, nie model.

    Wszystko poza polami zależnymi od scope'a (``name``, ``subpath``, ``labels``) jest stałe i żyje
    w adapterze silnika: obraz, ``command``, ``network_mode: none``, ``read_only``,
    ``no-new-privileges``, ``user 10001``, montaże wyliczone z ``subpath``. Jedyną zmienną
    pochodzącą z niezaufanego wejścia jest ``subpath`` (== zwalidowany ``scope``), więc to on i
    tylko on decyduje, KTÓRY podkatalog wolumenów zobaczy wykonawca.
    """

    name: str
    """Nazwa kontenera (Docker-bezpieczna, wyprowadzona ze scope'a) — także klucz idempotencji."""
    scope: str
    """Zwalidowany scope rozmowy (``<kanał>/<hash>``); ląduje w etykiecie do reconcile."""
    subpath: str
    """Podkatalog wolumenów montowany do wykonawcy (== ``scope``) — granica izolacji montażu."""
    labels: dict[str, str]
    """Etykiety Docker (m.in. scope + znacznik zarządzania) — po nich menedżer reconcile'uje."""


@dataclass(frozen=True)
class RunningExecutor:
    """Zarządzany kontener widziany przez silnik: id, scope z etykiety, obraz i czy jeszcze żyje.

    ``image`` i ``running`` są w tym kontrakcie, bo bez nich reconcile nie odróżnia wykonawcy,
    którego warto adoptować, od takiego, którego trzeba ubić — a obie pomyłki są ciche:

    - kontener z POPRZEDNIEGO obrazu, raz adoptowany, serwuje stary kod tak długo, jak długo
      rozmowa jest czynna (każde ``ensure`` odświeża ``last_used``, więc TTL nigdy nie dobiega).
      Podbicie obrazu wygląda wtedy na udane, a powłoka jedzie na wydaniu sprzed niego;
    - kontener ZATRZYMANY (np. ubity limitem pamięci) nie jest wykonawcą, tylko śmieciem po
      awarii: adopcja wpisałaby do rejestru trupa, a scope'y są per rozmowa, więc nikt by po
      niego nie wrócił.
    """

    container_id: str
    scope: str
    image: str
    """Obraz, z którego kontener FAKTYCZNIE powstał — porównywany z ``ContainerEngine.image``."""
    running: bool = True
    """Czy kontener nadal biegnie. ``False`` = do usunięcia, nigdy do adopcji."""


class ContainerEngine(Protocol):
    """Kontrakt menedżera nad silnikiem kontenerów — trzy czasowniki, żadnej powłoki."""

    @property
    def image(self) -> str:
        """Obraz, z którego silnik STAWIA wykonawców — punkt odniesienia adopcji przy reconcile.

        Wystawiony, bo obraz jest własnością szablonu (adapter), a decyzja „adoptować czy ubić"
        należy do menedżera (rdzeń). Bez tego rdzeń nie ma z czym porównać tego, co zastał.
        """
        ...

    def run(self, spec: ContainerSpec) -> str:
        """Postaw kontener-wykonawcę ze stałego szablonu; zwróć jego id. Podnieś przy odmowie."""
        ...

    def remove(self, container_id: str) -> None:
        """Usuń kontener (wymuś, jeśli żyje). Brak kontenera to nie błąd — cel osiągnięty."""
        ...

    def list_managed(self) -> list[RunningExecutor]:
        """Wypisz żywe kontenery z etykietą zarządzania — do reconcile po restarcie menedżera."""
        ...


class ScopeWorkspace(Protocol):
    """Warstwa systemu plików wspólna dla menedżera i wykonawcy: podkatalogi scope'a i gniazdo.

    Menedżer JEST właścicielem cyklu życia podkatalogu (ADR 0012 §4): tworzy go, zanim wystartuje
    kontener (inaczej Docker założyłby punkt bind-mountu jako pusty katalog roota → zły właściciel,
    zapis powłoki pada), i sprząta jego gniazdo po reap. ``socket_path`` liczy ścieżkę gniazda w
    układzie WIDZIANYM PRZEZ APLIKACJĘ — to ją menedżer oddaje w ``ensure``.
    """

    def prepare(self, scope: str) -> None:
        """Utwórz (idempotentnie) podkatalogi scope'a — brudnopis i gniazdo — z uid 10001."""
        ...

    def wait_ready(self, scope: str, timeout_s: float) -> bool:
        """Czekaj, aż wykonawca wystawi gniazdo; ``True`` gdy gotowe, ``False`` po oknie."""
        ...

    def socket_path(self, scope: str) -> str:
        """Ścieżka gniazda wykonawcy w układzie montaży APLIKACJI (dostaje ją klient ``Bash``)."""
        ...

    def cleanup(self, scope: str) -> None:
        """Sprzątnij katalog gniazda scope'a po reap. Brudnopis ZOSTAJE (sprząta go prune_stale)."""
        ...


class ExecManager(Protocol):
    """Kontrakt widziany przez APLIKACJĘ: zapewnij wykonawcę scope'a, zwróć ścieżkę jego gniazda."""

    def ensure(self, scope: str) -> str:
        """Zapewnij ciepłego wykonawcę scope'a (stawiając go przy pierwszym poleceniu) i zwróć
        ścieżkę jego gniazda. Idempotentne: ciepły wykonawca zwraca uchwyt natychmiast.
        Podnosi ``ExecManagerError``, gdy nie da się go zapewnić."""
        ...
