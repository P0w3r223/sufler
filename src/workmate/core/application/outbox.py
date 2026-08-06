"""Dostawa plików ze skrzynki nadawczej rozmowy.

Decyzja: ADR 0009 paczki wdrożeniowej `infra-docker-workmate`
(`docs/decyzje/0009-konsolidacja-powierzchni-narzedziowej.md`).

Po zakończeniu tury drzwi zaglądają do ``outputs/`` w katalogu roboczym rozmowy i wysyłają to,
co model tam zostawił. Serwis zależy wyłącznie od portów (``OutboxRepository`` + wstrzyknięty
``send``), więc reguła ``core ↛ adapters`` zostaje, a to, CZYM plik jedzie do rozmówcy, nie jest
tu wiedzą.

Reguła sprzątania rozróżnia dwa rodzaje niepowodzenia, bo mają przeciwne właściwe zachowania:

- **odrzucenie trwałe** — rozszerzenie spoza białej listy, plik ponad limit, ponad limit liczby,
  a także wysyłka odrzucona trwale (``PermanentDeliveryError``: 4xx z Graph, zniknięty root
  wątku). Plik ZNIKA ze skrzynki wraz z podaniem powodu. Zostawienie go zrobiłoby zatrutą
  wiadomość: ten sam komunikat doklejałby się do każdej kolejnej odpowiedzi w tej rozmowie, aż
  ktoś ręcznie wejdzie na wolumen. Nic się przy tym nie traci — skrzynka jest katalogiem
  PRZESYŁKOWYM, a materiał źródłowy leży w katalogu roboczym piętro wyżej;
- **awaria przejściowa** — sieć, 5xx, limit żądań. Plik ZOSTAJE, więc następna tura ponowi.
  Tu ubytek byłby realny: treść powstała, a odbiorca jej nie zobaczył.

Odczyt z dysku dotyka WYŁĄCZNIE pozycji, które przeszły kontrolę metadanych — zawartość skrzynki
dyktuje model z powłoką, a proces drzwi obsługuje wszystkie kanały naraz.

**Dostawa ma budżet czasu, bo biegnie w tej samej ścieżce co odpowiedź tury.** Rozmówca widzi
tekst dopiero, gdy wysyłka się skończy, a pięć plików po timeoucie klienta HTTP oznaczałoby
minuty ciszy po turze, która już się udała. Pozycje ponad budżet zostają w skrzynce i jadą przy
następnej wiadomości — opóźnienie jest ograniczone, a nic się nie gubi. Nie zmienia to
KOLEJNOŚCI: załączniki lądują w wątku przed tekstem, który je zapowiada, bo tekst wysyła poller
dopiero po powrocie z respondera. Odwrócenie tego wymaga zmiany kontraktu ``Responder``
(dziś: wiadomość → tekst) na wszystkich czworgu drzwiach i jest osobną decyzją.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from workmate.core.domain.workspace import safe_filename
from workmate.core.errors import WriteError
from workmate.core.ports.document import FILE_REPLY_FORMATS
from workmate.core.ports.outbox import (
    Deliverable,
    OutboxEntry,
    OutboxRepository,
    PermanentDeliveryError,
)

# Biała lista rozszerzeń skrzynki = ta sama, co narzędzia ``reply_with_file`` (ADR 0026).
# Jedno źródło: obie drogi kończą się załącznikiem w tym samym wątku, więc rozjazd oznaczałby,
# że format wolno wysłać jedną drogą, a drugą nie — bez powodu, który dałoby się wytłumaczyć.
_ALLOWED_EXT = frozenset(FILE_REPLY_FORMATS)

# Ile odrzuceń wymieniamy z nazwy w komunikacie. Reszta idzie zbiorczo: komunikat dokleja się do
# odpowiedzi, a wiadomość Teams ma sufit rozmiaru — trzysta zdań o odrzuconych plikach mogłoby
# sprawić, że rozmówca nie dostanie NICZEGO, choć pliki zostały już sprzątnięte.
_MAX_NAMED_REJECTIONS = 3

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboxLimits:
    """Granice jednej tury: rozmiar pliku, liczba plików i budżet czasu na całą dostawę."""

    max_file_bytes: int
    max_files_per_turn: int
    max_total_seconds: float = 20.0


@dataclass(frozen=True)
class DeliveryReport:
    """Co poszło, czego nie i dlaczego. ``rejected``/``failed`` niosą pary (nazwa, powód)."""

    delivered: tuple[str, ...] = ()
    rejected: tuple[tuple[str, str], ...] = ()
    failed: tuple[tuple[str, str], ...] = ()
    deferred: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (self.delivered or self.rejected or self.failed or self.deferred)

    def notice(self) -> str:
        """Zdanie doklejane do odpowiedzi; pusty napis, gdy nie było czego dostarczać.

        Odbiorcą jest CZŁOWIEK, nie model — tura już się skończyła, więc komunikat ma pozwolić
        zorientować się, co przyszło i czego zabrakło, a nie sterować kolejnym krokiem agenta.
        """
        parts: list[str] = []
        if self.delivered:
            parts.append("W załączniku: " + ", ".join(self.delivered) + ".")
        for name, reason in self.rejected[:_MAX_NAMED_REJECTIONS]:
            parts.append(f"Nie wysłałem pliku {name} — {reason}.")
        if len(self.rejected) > _MAX_NAMED_REJECTIONS:
            parts.append(f"Pominąłem też {len(self.rejected) - _MAX_NAMED_REJECTIONS} innych.")
        for name, reason in self.failed:
            parts.append(f"Nie udało się wysłać pliku {name} ({reason}) — spróbuję ponownie.")
        if self.deferred:
            parts.append(
                f"Zostało {len(self.deferred)} plików do wysłania — dostarczę je przy "
                "następnej wiadomości."
            )
        return " ".join(parts)


class OutboxDelivery:
    """Zabierz pliki ze skrzynki rozmowy i wyślij je wstrzykniętym ``send``."""

    def __init__(
        self,
        repo: OutboxRepository,
        limits: OutboxLimits,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repo = repo
        self._limits = limits
        # Nazwy widziane w skrzynce NA POCZĄTKU tury, per katalog rozmowy. Patrz ``snapshot``.
        self._at_start: dict[str, frozenset[str]] = {}
        # Nazwy, które MY zostawiliśmy po nieudanej wysyłce — jedyne wolno dostarczyć mimo
        # obecności w migawce, bo ich pochodzenie znamy.
        self._ours: dict[str, frozenset[str]] = {}
        # Zegar wstrzykiwany jak w responderze — testy mierzą budżet bez czekania realnego czasu.
        # MONOTONICZNY, bo mierzymy upływ, a nie porę: przestawienie zegara systemowego w środku
        # dostawy nie ma prawa jej urwać ani przedłużyć.
        self._monotonic = monotonic

    def snapshot(self, dirpath: str) -> None:
        """Zapamiętaj zawartość skrzynki PRZED turą — granica pochodzenia plików.

        Wolumen brudnopisu jest WSPÓLNY dla wszystkich rozmów, a wykonawca montuje go w całości
        i uruchamia polecenia bez chroota — `cwd` jest konwencją, nie zamknięciem. Powłoka
        rozmowy A może więc policzyć katalog rozmowy B (`sha256(team/channel/root)`, a trójka
        jest jawna dla każdego w kanale), założyć w nim ``outputs/`` zwykłym ``mkdir`` i podłożyć
        plik. Bez tej migawki kolektor opublikowałby go w CUDZYM wątku, firmując treść botem —
        i żaden guard na dowiązania by tego nie dotknął, bo dowiązania tam nie ma.

        Migawka zamyka to, bo **tury są szeregowane**: poller robi ``await self._handle`` w pętli
        sekwencyjnej, więc powłoka rozmowy A nie biegnie w trakcie tury rozmowy B. Plik podłożony
        wcześniej jest w migawce i nie zostanie wysłany; plik powstały w trakcie tury — zostanie.
        """
        self._at_start[dirpath] = frozenset(e.name for e in self._repo.list_entries(dirpath))

    def deliver(self, dirpath: str, send: Callable[[Deliverable], None]) -> DeliveryReport:
        """Wyślij zawartość skrzynki ``dirpath``; zwróć raport, nigdy nie podnoś wyjątku wysyłki.

        Wyjątek z ``send`` jest ŁAPANY i zamieniany w pozycję raportu: dostawa jest dodatkiem
        do tury, która już się udała, więc jej awaria nie może zabrać rozmówcy odpowiedzi
        tekstowej. Błąd odczytu samej skrzynki propaguje — to defekt montażu, nie treści.
        """
        if dirpath not in self._at_start:
            # FAIL-CLOSED. Bez migawki nie umiemy odróżnić pliku od modelu tej rozmowy od
            # podłożonego przez inną, więc nie wysyłamy nic. Wołający ma zawołać ``snapshot``
            # na starcie tury; brak wywołania jest defektem okablowania, nie stanem normalnym.
            logger.error("Dostawa dla %s bez migawki startowej — pomijam całą skrzynkę.", dirpath)
            return DeliveryReport()
        at_start = self._at_start.pop(dirpath)
        ours = self._ours.get(dirpath, frozenset())

        entries = sorted(self._repo.list_entries(dirpath), key=lambda e: e.name)
        foreign = [e for e in entries if e.name in at_start and e.name not in ours]
        if foreign:
            # Poza komunikatem dla rozmówcy: nie spowodował tego i nie ma jak zareagować.
            # Sprzątamy — pozostawienie dawałoby to samo ostrzeżenie przy KAŻDEJ kolejnej turze.
            logger.warning(
                "Skrzynka %s zawierała %d plików sprzed tury (%s) — nie wysyłam ich. "
                "Albo to pozostałość sprzed restartu procesu, albo podłożenie z innej rozmowy.",
                dirpath,
                len(foreign),
                ", ".join(e.name for e in foreign),
            )
            for entry in foreign:
                self._repo.discard(dirpath, entry.name)
        entries = [e for e in entries if e not in foreign]
        if not entries:
            self._ours.pop(dirpath, None)
            return DeliveryReport()

        delivered: list[str] = []
        rejected: list[tuple[str, str]] = []
        failed: list[tuple[str, str]] = []
        deferred: list[str] = []

        for entry in entries[self._limits.max_files_per_turn :]:
            rejected.append(
                (entry.name, f"na turę wysyłam najwyżej {self._limits.max_files_per_turn} plików")
            )
            self._repo.discard(dirpath, entry.name)

        deadline = self._monotonic() + self._limits.max_total_seconds
        for entry in entries[: self._limits.max_files_per_turn]:
            reason = self._rejection(entry)
            if reason is not None:
                rejected.append((entry.name, reason))
                self._repo.discard(dirpath, entry.name)
                continue
            # Budżet sprawdzamy PRZED wysyłką, nie po: dostawa biegnie w tej samej ścieżce co
            # odpowiedź tury, więc pięć plików po timeoucie klienta HTTP wstrzymywałoby rozmówcę
            # minutami. Pozycje ponad budżet zostają w skrzynce i jadą przy następnej wiadomości —
            # opóźnienie jest ograniczone, a nic się nie gubi.
            if self._monotonic() >= deadline:
                deferred.append(entry.name)
                continue
            item = self._repo.read(dirpath, entry.name)
            if item is None:
                # Plik zniknął między wypisem a odczytem — nie ma czego wysyłać ani sprzątać.
                continue
            outcome = self._send_one(item, send)
            if outcome is None:
                delivered.append(item.name)
                self._repo.discard(dirpath, entry.name)
            elif outcome[0]:
                rejected.append((entry.name, outcome[1]))
                self._repo.discard(dirpath, entry.name)
            else:
                failed.append((entry.name, outcome[1]))

        # Zapamiętaj, co ZOSTAWILIŚMY — tylko te nazwy wolno wysłać w kolejnej turze mimo
        # obecności w migawce. Pusty zbiór usuwamy, żeby słownik nie rósł z liczbą rozmów.
        retained = frozenset([name for name, _ in failed] + deferred)
        if retained:
            self._ours[dirpath] = retained
        else:
            self._ours.pop(dirpath, None)
        return DeliveryReport(tuple(delivered), tuple(rejected), tuple(failed), tuple(deferred))

    def _send_one(
        self, item: Deliverable, send: Callable[[Deliverable], None]
    ) -> tuple[bool, str] | None:
        """Wyślij pozycję; ``None`` przy sukcesie, inaczej (czy trwałe, powód)."""
        try:
            send(replace(item, name=safe_filename(item.name, allowed_ext=_ALLOWED_EXT)))
        except PermanentDeliveryError as exc:
            return (True, str(exc) or "odbiorca odrzucił plik")
        except Exception as exc:  # noqa: BLE001 — patrz docstring: raport zamiast wyjątku
            return (False, type(exc).__name__)
        return None

    def _rejection(self, entry: OutboxEntry) -> str | None:
        """Powód odrzucenia trwałego albo ``None``, gdy pozycja nadaje się do wysłania.

        Rozstrzygamy na METADANYCH, przed dotknięciem treści — plik ponad limit ma nie trafić
        do pamięci procesu tylko po to, żeby zaraz zostać odrzuconym.
        """
        if entry.size == 0:
            return "jest pusty"
        if entry.size > self._limits.max_file_bytes:
            limit_kb = self._limits.max_file_bytes // 1024
            return f"przekracza limit {limit_kb} KB"
        try:
            safe_filename(entry.name, allowed_ext=_ALLOWED_EXT)
        except WriteError:
            allowed = ", ".join(sorted(_ALLOWED_EXT))
            return f"ma nazwę spoza dozwolonych (rozszerzenia: {allowed})"
        return None
