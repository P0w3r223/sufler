"""Dostawa plików ze skrzynki nadawczej rozmowy (ADR 0009 paczki wdrożeniowej).

Po zakończeniu tury drzwi zaglądają do ``outputs/`` w katalogu roboczym rozmowy i wysyłają to,
co model tam zostawił. Serwis zależy wyłącznie od portów (``OutboxRepository`` + wstrzyknięty
``send``), więc reguła ``core ↛ adapters`` zostaje, a to, CZYM plik jedzie do rozmówcy —
załącznikiem Graph, czymkolwiek innym — nie jest tu wiedzą.

Reguła sprzątania rozróżnia dwa rodzaje niepowodzenia, bo mają przeciwne właściwe zachowania:

- **odrzucenie trwałe** (rozszerzenie spoza białej listy, plik ponad limit) — plik ZNIKA ze
  skrzynki wraz z podaniem powodu. Zostawienie go zrobiłoby zatrutą wiadomość: ten sam komunikat
  doklejałby się do każdej kolejnej odpowiedzi w tej rozmowie, aż ktoś ręcznie wejdzie na wolumen.
  Nic się przy tym nie traci — skrzynka jest katalogiem PRZESYŁKOWYM, a materiał źródłowy leży
  w katalogu roboczym piętro wyżej;
- **awaria wysyłki** (Graph nie przyjął) — plik ZOSTAJE, więc następna tura ponowi. Tu ubytek
  byłby realny: treść powstała, a odbiorca jej nie zobaczył.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from workmate.core.domain.workspace import safe_filename
from workmate.core.errors import WriteError
from workmate.core.ports.document import FILE_REPLY_FORMATS
from workmate.core.ports.outbox import Deliverable, OutboxRepository

# Biała lista rozszerzeń skrzynki = ta sama, co narzędzia ``reply_with_file`` (ADR 0026).
# Jedno źródło: obie drogi kończą się załącznikiem w tym samym wątku, więc rozjazd oznaczałby,
# że format wolno wysłać jedną drogą, a drugą nie — bez powodu, który dałoby się wytłumaczyć.
_ALLOWED_EXT = frozenset(FILE_REPLY_FORMATS)


@dataclass(frozen=True)
class OutboxLimits:
    """Granice jednej tury: rozmiar pojedynczego pliku i liczba plików."""

    max_file_bytes: int
    max_files_per_turn: int


@dataclass(frozen=True)
class DeliveryReport:
    """Co poszło, czego nie i dlaczego. ``rejected``/``failed`` niosą pary (nazwa, powód)."""

    delivered: tuple[str, ...] = ()
    rejected: tuple[tuple[str, str], ...] = ()
    failed: tuple[tuple[str, str], ...] = ()

    def is_empty(self) -> bool:
        return not (self.delivered or self.rejected or self.failed)

    def notice(self) -> str:
        """Zdanie doklejane do odpowiedzi; pusty napis, gdy nie było czego dostarczać.

        Odbiorcą jest CZŁOWIEK, nie model — tura już się skończyła, więc komunikat ma pozwolić
        zorientować się, co przyszło i czego zabrakło, a nie sterować kolejnym krokiem agenta.
        """
        parts: list[str] = []
        if self.delivered:
            parts.append("W załączniku: " + ", ".join(self.delivered) + ".")
        for name, reason in self.rejected:
            parts.append(f"Nie wysłałem pliku {name} — {reason}.")
        for name, reason in self.failed:
            parts.append(f"Nie udało się wysłać pliku {name} ({reason}) — spróbuję ponownie.")
        return " ".join(parts)


class OutboxDelivery:
    """Zabierz pliki ze skrzynki rozmowy i wyślij je wstrzykniętym ``send``."""

    def __init__(self, repo: OutboxRepository, limits: OutboxLimits) -> None:
        self._repo = repo
        self._limits = limits

    def deliver(self, dirpath: str, send: Callable[[Deliverable], None]) -> DeliveryReport:
        """Wyślij zawartość skrzynki ``dirpath``; zwróć raport, nigdy nie podnoś wyjątku wysyłki.

        Wyjątek z ``send`` jest ŁAPANY i zamieniany w pozycję ``failed``: dostawa jest dodatkiem
        do tury, która już się udała, więc jej awaria nie może zabrać rozmówcy odpowiedzi
        tekstowej. Błąd odczytu samej skrzynki propaguje — to defekt montażu, nie treści.
        """
        candidates = sorted(self._repo.collect(dirpath), key=lambda d: d.name)
        if not candidates:
            return DeliveryReport()

        delivered: list[str] = []
        rejected: list[tuple[str, str]] = []
        failed: list[tuple[str, str]] = []

        over_cap = candidates[self._limits.max_files_per_turn :]
        for item in over_cap:
            rejected.append(
                (item.name, f"na turę wysyłam najwyżej {self._limits.max_files_per_turn} plików")
            )
            self._repo.discard(dirpath, item.name)

        for item in candidates[: self._limits.max_files_per_turn]:
            reason = self._rejection(item)
            if reason is not None:
                rejected.append((item.name, reason))
                self._repo.discard(dirpath, item.name)
                continue
            try:
                send(item)
            except Exception as exc:  # noqa: BLE001 — patrz docstring: raport zamiast wyjątku
                failed.append((item.name, type(exc).__name__))
                continue
            delivered.append(item.name)
            self._repo.discard(dirpath, item.name)

        return DeliveryReport(tuple(delivered), tuple(rejected), tuple(failed))

    def _rejection(self, item: Deliverable) -> str | None:
        """Powód odrzucenia trwałego albo ``None``, gdy plik nadaje się do wysłania."""
        if not item.content:
            return "jest pusty"
        if len(item.content) > self._limits.max_file_bytes:
            limit_kb = self._limits.max_file_bytes // 1024
            return f"przekracza limit {limit_kb} KB"
        try:
            safe_filename(item.name, allowed_ext=_ALLOWED_EXT)
        except WriteError:
            allowed = ", ".join(sorted(_ALLOWED_EXT))
            return f"ma rozszerzenie spoza listy (dozwolone: {allowed})"
        return None
