"""Tryb demo: narzędzie bez rejestru, na syntetycznych danych (ADR-0014).

Powód jest szerszy niż pokaz. Środowiska `test` nie ma — `test-dane.biznes.gov.pl` nie
odpowiada (2 802 żądania produkcyjne w historii, **zero** testowych), a token CEIDG wymaga
Profilu Zaufanego. Nowy właściciel tego repozytorium nie mógł dotąd uruchomić narzędzia ani
razu bez prawdziwych danych osobowych. Tryb demo zamyka to na stałe, a pokaz jest produktem
ubocznym — dlatego mieszka w pakiecie, a nie w osobnym skrypcie, który zgniłby między pokazami.

**Reguła granic 11 zostaje nietknięta.** Klienta HTTP nadal buduje wyłącznie `httpclient`;
tutaj powstaje tylko `httpx.MockTransport`, który przez ten sam `build_http_client` przechodzi
i tak samo zostaje opakowany w `AllowedHostsTransport`. Gniazdo nie otwiera się w ogóle, więc
bramka wyjścia nie jest omijana — nie ma dokąd wychodzić. `build_deps` nie zna trybu demo:
podstawienie zachodzi w korzeniu kompozycji (`cli`), bo `tests/resilience/test_egress_allowlist.py`
słusznie pilnuje, żeby produkcja wołała `build_http_client(transport=None)`.

**Czego demo nie udaje.** Korpus ma kilkaset wpisów, a nie 6 316 121 (tyle rejestr zwrócił
przy `limit=1`, `docs/decisions.md`), więc liczby w tabeli
kosztów są prawdziwe dla korpusu, nie dla rejestru. Zegar jest ściśnięty, żeby pokaz mieścił
się w minutach — ale wycena nadal drukuje **prawdziwe** minuty, bo `estimating.estimate` jest
czystą funkcją liczby trafień i profilu. To jest kotwica uczciwości tego demo i warto ją
powiedzieć na głos, pokazując tabelę.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from ..clock import Clock
from ..errors import ConfigError
from ..httpclient import build_http_client
from .korpus import Korpus, zbuduj_korpus
from .rejestr import RejestrDemo

__all__ = ["DEMO_PRZYSPIESZENIE", "TOKEN_DEMO", "ZegarDemo", "Demo", "zbuduj_demo"]

# Token zastępczy. Nie jest sekretem i nie ma nim być: nic go nie sprawdza, bo żądania nie
# opuszczają procesu. Jest tu po to, żeby `load_settings` nie sięgnęło po prawdziwy token
# z `.env` albo z keyringu — demo uruchomione w katalogu repozytorium wczytałoby poświadczenie
# niosące PESEL, i to jest jedyny powód istnienia tej stałej.
TOKEN_DEMO = "demo-bez-rejestru"

# Ile razy szybciej płynie czas w demie. **Nie zero i nie „bardzo dużo".** Zegar zerowy
# kończyłby pobieranie, zanim widownia zdąży spojrzeć, nie zostawiałby okna na ubicie procesu
# i ukrywałby całą maszynerię — limiter, bicie serca, rytm paska — która istnieje właśnie po to,
# żeby długie oczekiwanie było znośne.
#
# Osiem razy daje z odstępu 3,75 s około 0,47 s na żądanie, czyli pełne pobranie ze
# szczegółami (240 wpisów: 10 stron listy + 48 porcji szczegółów) trwa około pół minuty.
# Tyle wystarczy, żeby pasek było widać i żeby dało się proces ubić w połowie. Przy
# trzydziestu razach całość mijała w siedem sekund i scenariusz wznowienia był nie do pokazania.
DEMO_PRZYSPIESZENIE = 8.0
ENV_TEMPO = "CEIDG_DEMO_TEMPO"
# Górny limit tempa. Powyżej pewnego progu `sleep` skraca się poniżej rozdzielczości zegara
# systemowego i limiter przestaje widzieć upływ czasu — kończy się to `LimiterStalledError`
# w połowie pobierania, z komunikatem każącym sprawdzić zegar systemowy i bazę historii
# żądań. To poprawna diagnoza dla awarii, o której mówi, i myląca dla operatora, który
# właśnie przekręcił to pokrętło. Zmierzone: 20 000 działa, 100 000 wywraca przebieg;
# próg zależy od maszyny, więc limit stoi z zapasem, a odmowa nazywa przyczynę.
MAKS_TEMPO = 5_000.0


class ZegarDemo:
    """Zegar, który idzie naprawdę, tylko szybciej.

    `sleep` śpi skróconą chwilę, a nie wcale: dzięki temu limiter, `LockHeartbeat` i pasek
    postępu wykonują **te same** ścieżki co na produkcji, w tej samej kolejności. Zegar
    całkowicie sztuczny (zwracający czas bez spania) byłby szybszy i pokazywałby inny program.
    """

    def __init__(self, bazowy: Clock, przyspieszenie: float = DEMO_PRZYSPIESZENIE) -> None:
        self._bazowy = bazowy
        self._przyspieszenie = max(przyspieszenie, 1.0)

    def monotonic(self) -> float:
        return self._bazowy.monotonic() * self._przyspieszenie

    def wall(self) -> float:
        # Czas ścienny **nie** jest skalowany: idzie do znaczników w bazie, do nazw plików
        # i do komunikatu „wznawiam o HH:MM". Przyspieszony dawałby datę z przyszłości,
        # a `update_range` słusznie odmawia zakresu kończącego się w przyszłości.
        return self._bazowy.wall()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._bazowy.sleep(seconds / self._przyspieszenie)


@dataclass(frozen=True)
class Demo:
    """Komplet potrzebny, żeby poprowadzić narzędzie bez rejestru."""

    klient: httpx.Client
    zegar: Clock
    korpus: Korpus
    rejestr: RejestrDemo


def zbuduj_demo(*, zegar: Clock, host: str, ile: int = 240) -> Demo:
    """Klient HTTP odpowiadający z syntetycznego rejestru, plus ściśnięty zegar.

    `host` zawęża bramkę wyjścia dokładnie tak, jak robi to `build_deps` dla produkcji —
    atrapa jedzie tą samą ścieżką co ruch prawdziwy, bo szew, którym testy omijają produkcję,
    już raz ukrył defekt (`trust_env` w `httpclient`, 2026-09-07).
    """
    korpus = zbuduj_korpus(ile=ile)
    tempo = _tempo()
    rejestr = RejestrDemo(korpus)
    klient = build_http_client(
        transport=httpx.MockTransport(rejestr.handler), allowed=frozenset({host})
    )
    return Demo(klient=klient, zegar=ZegarDemo(zegar, tempo), korpus=korpus, rejestr=rejestr)


def _tempo() -> float:
    """Tempo pokazu z `CEIDG_DEMO_TEMPO`, gdy domyślne pół minuty nie pasuje do widowni.

    Zmienna, a nie flaga: to pokrętło reżyserii pokazu, a nie decyzja o danych, i nie ma
    powodu, żeby powiększało powierzchnię poleceń, których operator używa do pracy."""
    surowe = os.environ.get(ENV_TEMPO, "").strip()
    if not surowe:
        return DEMO_PRZYSPIESZENIE
    try:
        zadane = float(surowe)
    except ValueError as exc:
        raise ConfigError(
            f"{ENV_TEMPO}={surowe!r} nie jest liczbą. Podaj ile razy szybciej ma płynąć czas "
            f"pokazu, od 1 do {MAKS_TEMPO:.0f}."
        ) from exc
    if not 1.0 <= zadane <= MAKS_TEMPO:
        raise ConfigError(
            f"{ENV_TEMPO}={surowe} jest poza zakresem 1–{MAKS_TEMPO:.0f}. Przy wyższym tempie "
            "przerwy schodzą poniżej rozdzielczości zegara systemowego i limiter przerywa "
            "pobranie, obwiniając zegar zamiast tej zmiennej."
        )
    return zadane
