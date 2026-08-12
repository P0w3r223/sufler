"""Cykl życia wykonawców per rozmowa (ADR infra 0012) — czysta logika menedżera.

Ten moduł jest sercem menedżera i CELOWO nie wie nic o Dockerze ani o systemie plików: dostaje
``ContainerEngine`` i ``ScopeWorkspace`` przez konstruktor, więc reguły (idempotencja ``ensure``,
limit ``N`` z eksmisją LRU, reap po TTL, reconcile po etykiecie) testują się na atrapach, bez
demona.

Model współbieżności jest prosty rozmyślnie: JEDEN zamek serializuje wszystkie operacje menedżera.
Powłoka jest rzadka (układ A produkcji: 0× ``Bash``), a poller i tak szereguje tury (ADR 0010) —
realna równoległość ≈ liczba aktywnych kanałów, nie nieograniczona. Trzymanie zamka przez cały
``ensure`` (łącznie z oczekiwaniem na gotowość gniazda) usuwa wyścig „dwa polecenia stawiają dwa
kontenery tego samego scope'a" kosztem, na który przy tej częstości powłoki stać nas z zapasem.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from workmate.core.errors import ExecManagerError

if TYPE_CHECKING:
    from collections.abc import Callable

    from workmate.core.ports.exec_manager import (
        ContainerEngine,
        ContainerSpec,
        ScopeWorkspace,
    )

logger = logging.getLogger(__name__)

# Scope = ``<kanał>/<hash>`` z ``WorkspaceScope.dirpath()``: kanał slugowany do ``[a-z0-9-]``,
# hash to 32 znaki hex (sha256 id rozmowy). Walidacja jest GRANICĄ bezpieczeństwa, nie kosmetyką:
# scope trafia do ``Subpath`` montażu Docker API, więc cokolwiek spoza tego alfabetu (``..``, ``/``
# w nadmiarze, znaki powłoki) mogłoby wskazać podkatalog spoza wolumenu brudnopisu. Kotwice
# ``^``/``$``
# i dokładnie jeden ``/`` są tu istotą — nie wystarczy „zawiera dozwolone znaki".
_SCOPE_RE = re.compile(r"^[a-z0-9-]{1,64}/[0-9a-f]{32}$")

# Prefiks nazwy i etykiety kontenera-wykonawcy. Nazwa jest Docker-bezpieczna (spłaszczony scope),
# etykiety niosą scope w postaci oryginalnej — po nich biegnie reconcile.
_NAME_PREFIX = "workmate-exec-"
_LABEL_MANAGED = "workmate.exec.managed"
_LABEL_SCOPE = "workmate.exec.scope"
_LABEL_RUN_ID = "workmate.exec.run_id"


def validate_scope(scope: str) -> None:
    """Podnieś ``ExecManagerError``, gdy scope nie jest dokładnie ``<kanał>/<hash>`` z alfabetu.

    Wołane PRZED jakimkolwiek dotknięciem silnika czy systemu plików: zły scope nie ma prawa
    dojść do żądania Docker API ani do ``mkdir``.
    """
    if not _SCOPE_RE.match(scope):
        raise ExecManagerError(f"scope wykonawcy jest niepoprawny: {scope!r}")


def _container_name(scope: str) -> str:
    """Nazwa kontenera ze scope'a: ``<prefix><kanał>-<hash>`` (``/`` → ``-``, Docker-bezpieczne).

    Odwzorowanie jest jednoznaczne (kanał bez ``/``, hash stałej długości), więc dwa różne scope'y
    nie zderzą się nazwą — a nazwa jest zarazem kluczem idempotencji po stronie silnika.
    """
    return _NAME_PREFIX + scope.replace("/", "-")


@dataclass
class _ManagedExecutor:
    """Wpis rejestru: scope, id kontenera i znacznik ostatniego użycia (zegar monotoniczny)."""

    scope: str
    container_id: str
    last_used: float


class ExecManagerService:
    """Menedżer cyklu życia wykonawców: ``ensure`` on-demand, reap po TTL, limit N (LRU), reconcile.

    ``max_executors`` to górny limit równoległych wykonawców (parametr, nie decyzja ADR — patrz
    ADR 0012 §3). ``idle_ttl_s`` to okno bezczynności po którym wykonawca jest reapowany, zestrojone
    z ``prune_stale`` katalogu roboczego, żeby wykonawca i jego dane znikały jednym zegarem.
    ``clock`` jest wstrzykiwany (monotoniczny) — testy podają własny, by sterować TTL/LRU bez
    ``sleep``.
    """

    def __init__(
        self,
        engine: ContainerEngine,
        workspace: ScopeWorkspace,
        *,
        run_id: str,
        max_executors: int = 8,
        idle_ttl_s: float = 900.0,
        ready_timeout_s: float = 15.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_executors < 1:
            raise ValueError(f"max_executors musi być >= 1, jest: {max_executors}")
        self._engine = engine
        self._workspace = workspace
        self._run_id = run_id
        self._max = max_executors
        self._ttl = idle_ttl_s
        self._ready_timeout_s = ready_timeout_s
        self._clock = clock if clock is not None else _monotonic
        self._by_scope: dict[str, _ManagedExecutor] = {}
        # Jeden zamek na cały menedżer — patrz nota modułu o modelu współbieżności.
        self._lock = threading.RLock()

    def ensure(self, scope: str) -> str:
        """Zapewnij ciepłego wykonawcę scope'a i zwróć ścieżkę jego gniazda (widzianą przez
        aplikację).

        Kolejność (ADR 0012 §4, „montaż bez wyścigu"): walidacja → jeśli ciepły, dotknij i wróć →
        w innym razie zrób miejsce (limit N), przygotuj podkatalogi, postaw kontener, POCZEKAJ na
        gotowość gniazda i dopiero wtedy zwróć ścieżkę. Polecenie nigdy nie leci przed gotowością,
        bo ścieżkę oddajemy dopiero po ``wait_ready``.
        """
        validate_scope(scope)
        with self._lock:
            existing = self._by_scope.get(scope)
            if existing is not None:
                existing.last_used = self._clock()
                return self._workspace.socket_path(scope)

            self._make_room_locked(scope)
            self._workspace.prepare(scope)
            spec = self._spec_for(scope)
            container_id = self._engine.run(spec)
            # Rejestrujemy PRZED oczekiwaniem na gotowość: gdyby readiness padło, ``_start_failed``
            # ma po czym posprzątać, a reconcile/reap nie zobaczy kontenera-widma bez wpisu.
            self._by_scope[scope] = _ManagedExecutor(scope, container_id, self._clock())

            if not self._workspace.wait_ready(scope, self._ready_timeout_s):
                self._start_failed_locked(scope, container_id)
                raise ExecManagerError(
                    f"wykonawca scope {scope!r} nie wystawił gniazda w oknie "
                    f"{self._ready_timeout_s:.0f}s"
                )
            logger.info("Wykonawca scope %s gotowy (%s)", scope, container_id[:12])
            return self._workspace.socket_path(scope)

    def reap(self, scope: str) -> None:
        """Ubij wykonawcę scope'a i sprzątnij jego gniazdo (jawny odpowiednik reap po TTL)."""
        with self._lock:
            executor = self._by_scope.pop(scope, None)
        if executor is None:
            return
        self._teardown(executor)

    def reap_idle(self) -> int:
        """Ubij wykonawców bezczynnych dłużej niż ``idle_ttl_s``; zwróć ile ubito.

        Wołane cyklicznie z zegara menedżera. Zwracana liczba służy logom/testom — sam efekt to
        zgaszone kontenery i sprzątnięte gniazda.
        """
        now = self._clock()
        with self._lock:
            stale = [
                executor
                for executor in self._by_scope.values()
                if now - executor.last_used > self._ttl
            ]
            for executor in stale:
                del self._by_scope[executor.scope]
        for executor in stale:
            self._teardown(executor)
        if stale:
            logger.info("Reap po TTL: zgaszono %d bezczynnych wykonawców", len(stale))
        return len(stale)

    def reconcile(self) -> None:
        """Po starcie menedżera pogódź rejestr z rzeczywistością silnika (ADR 0012 §3).

        Rejestr startuje pusty, więc każdy żywy zarządzany kontener to ślad po POPRZEDNIM
        menedżerze.
        Wykonawców z poprawną etykietą scope ADOPTUJEMY (żeby restart menedżera nie zabił ciepłego
        wykonawcy obsługującego czynną rozmowę), a sieroty bez czytelnego scope UBIJAMY (nikt ich
        już
        nie pilnuje). Adoptowany dostaje świeży ``last_used`` — inaczej wpadłby od razu w reap.
        """
        try:
            managed = self._engine.list_managed()
        except Exception:  # noqa: BLE001 — reconcile nie może wywrócić startu menedżera
            logger.warning("Reconcile: nie udało się wypisać wykonawców — pomijam", exc_info=True)
            return
        adopted = 0
        for running in managed:
            if _SCOPE_RE.match(running.scope):
                with self._lock:
                    self._by_scope[running.scope] = _ManagedExecutor(
                        running.scope, running.container_id, self._clock()
                    )
                adopted += 1
            else:
                logger.warning(
                    "Reconcile: sierota bez czytelnego scope (%s) — ubijam",
                    running.container_id[:12],
                )
                self._safe_remove(running.container_id)
        logger.info("Reconcile: adoptowano %d wykonawców", adopted)

    def shutdown(self) -> None:
        """Ubij wszystkich znanych wykonawców — sprzątanie przy zatrzymaniu menedżera."""
        with self._lock:
            executors = list(self._by_scope.values())
            self._by_scope.clear()
        for executor in executors:
            self._teardown(executor)

    # ── wewnętrzne ────────────────────────────────────────────────────────────

    def _spec_for(self, scope: str) -> ContainerSpec:
        from workmate.core.ports.exec_manager import ContainerSpec

        return ContainerSpec(
            name=_container_name(scope),
            scope=scope,
            subpath=scope,
            labels={
                _LABEL_MANAGED: "1",
                _LABEL_SCOPE: scope,
                _LABEL_RUN_ID: self._run_id,
            },
        )

    def _make_room_locked(self, scope: str) -> None:
        """Zrób miejsce na nowego wykonawcę: eksmituj LRU, dopóki jest pod limitem.

        „Zajętość" nie jest śledzona osobno (tury i tak są szeregowane wyżej — ADR 0010), więc
        LRU liczymy po ``last_used``. Eksmisja usuwa NAJDAWNIEJ używanego, aż zrobi się miejsce.
        """
        while len(self._by_scope) >= self._max:
            victim = min(self._by_scope.values(), key=lambda e: e.last_used)
            del self._by_scope[victim.scope]
            logger.info(
                "Limit N=%d osiągnięty — eksmituję LRU scope %s pod %s",
                self._max,
                victim.scope,
                scope,
            )
            self._teardown(victim)

    def _start_failed_locked(self, scope: str, container_id: str) -> None:
        """Wycofaj nieudany start: zdejmij wpis (jeśli to wciąż ten kontener) i ubij kontener."""
        current = self._by_scope.get(scope)
        if current is not None and current.container_id == container_id:
            del self._by_scope[scope]
        self._safe_remove(container_id)
        self._workspace.cleanup(scope)

    def _teardown(self, executor: _ManagedExecutor) -> None:
        """Ubij kontener i sprzątnij gniazdo scope'a. Wołane spoza zamka (I/O może być wolne)."""
        self._safe_remove(executor.container_id)
        try:
            self._workspace.cleanup(executor.scope)
        except OSError:
            logger.warning(
                "Nie udało się sprzątnąć gniazda scope %s", executor.scope, exc_info=True
            )

    def _safe_remove(self, container_id: str) -> None:
        try:
            self._engine.remove(container_id)
        except Exception:  # noqa: BLE001 — usuwanie best-effort; brak kontenera to cel, nie błąd
            logger.warning("Nie udało się usunąć kontenera %s", container_id[:12], exc_info=True)


def _monotonic() -> float:
    import time

    return time.monotonic()
