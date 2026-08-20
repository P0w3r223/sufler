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
        RunningExecutor,
        ScopeWorkspace,
    )

logger = logging.getLogger(__name__)

# Scope = ``<kanał>/<hash>`` z ``WorkspaceScope.dirpath()``: kanał slugowany do ``[a-z0-9-]``,
# hash to 32 znaki hex (sha256 id rozmowy). Walidacja jest GRANICĄ bezpieczeństwa, nie kosmetyką:
# scope trafia do ``Subpath`` montażu Docker API, więc cokolwiek spoza tego alfabetu (``..``, ``/``
# w nadmiarze, znaki powłoki) mogłoby wskazać podkatalog spoza wolumenu brudnopisu. Kotwice
# ``^``/``\Z`` i dokładnie jeden ``/`` są tu istotą — nie wystarczy „zawiera dozwolone znaki".
# Świadomie ``\Z`` (koniec napisu), NIE ``$``: ``$`` dopuszcza końcowy ``\n``, więc scope
# ``kanał/<hash>\n`` przeszedłby walidację i zdążyłby założyć katalog-śmieć, zanim Docker odrzuci
# nazwę kontenera z nową linią.
_SCOPE_RE = re.compile(r"^[a-z0-9-]{1,64}/[0-9a-f]{32}\Z")

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
        bo ścieżkę oddajemy dopiero po ``wait_ready`` — a całość biegnie pod zamkiem, więc dwa
        polecenia tej samej rozmowy nie postawią dwóch kontenerów, a klient nie dostanie ścieżki,
        zanim gniazdo nasłuchuje.

        Eksmisję LRU (limit N) tylko ODNOTOWUJEMY pod zamkiem (zdejmujemy z rejestru), a właściwe
        gaszenie ofiar — z jego wolnym I/O Dockera — robimy w ``finally`` POZA zamkiem, żeby jeden
        `ensure` z eksmisją nie stallował rozmów innych scope'ów przez czas usuwania kontenera.
        """
        validate_scope(scope)
        victims: list[_ManagedExecutor] = []
        container_id = ""
        ready = False
        try:
            with self._lock:
                existing = self._by_scope.get(scope)
                if existing is not None:
                    existing.last_used = self._clock()
                    return self._workspace.socket_path(scope)

                victims = self._evict_for_room_locked(scope)
                self._workspace.prepare(scope)
                spec = self._spec_for(scope)
                container_id = self._engine.run(spec)
                # Rejestrujemy PRZED oczekiwaniem na gotowość, żeby reconcile/reap nie zobaczył
                # kontenera-widma bez wpisu; przy porażce readiness zdejmujemy wpis niżej.
                self._by_scope[scope] = _ManagedExecutor(scope, container_id, self._clock())
                ready = self._workspace.wait_ready(scope, self._ready_timeout_s)
                if not ready:
                    current = self._by_scope.get(scope)
                    if current is not None and current.container_id == container_id:
                        del self._by_scope[scope]
        finally:
            # Ofiary eksmisji gaszimy POZA zamkiem (Docker `remove` bywa wolny). Każde
            # ``_teardown`` sprząta gniazdo dopiero, gdy scope nie został re-ensure'owany.
            for victim in victims:
                self._teardown(victim)

        if not ready:
            self._safe_remove(container_id)
            self._cleanup_if_free(scope)
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

        Rejestr startuje pusty, więc każdy zarządzany kontener to ślad po POPRZEDNIM menedżerze.
        Adoptujemy WYŁĄCZNIE takiego, którego umielibyśmy dziś postawić sami: żywego, z czytelnym
        scope'em, z BIEŻĄCEGO obrazu, jedynego dla swojej rozmowy i mieszczącego się w limicie N.
        Wszystko inne ubijamy. Cold start kosztuje jeden ``docker run``; każda z tych pomyłek jest
        cicha i trwała. Adoptowany dostaje świeży ``last_used`` — inaczej wpadłby od razu w reap.

        **Adopcja kontenera z POPRZEDNIEGO obrazu jest tu najgroźniejsza i najmniej widoczna.**
        Dopóki ``list_managed`` było zepsute (rzucało zawsze), rejestr zostawał pusty i pierwszy
        ``ensure`` stawiał wykonawcę od nowa — awaria przypadkiem gwarantowała poprawne wdrożenie.
        Po jej naprawie adopcja bez sprawdzenia obrazu serwowałaby STARY kod tak długo, jak długo
        rozmowa jest czynna: każde ``ensure`` odświeża ``last_used``, więc TTL nigdy nie dobiega.
        Podbicie obrazu wyglądałoby na udane, a powłoka jechałaby na wydaniu sprzed niego.
        """
        try:
            managed = self._engine.list_managed()
        except Exception:  # noqa: BLE001 — reconcile nie może wywrócić startu menedżera
            logger.warning("Reconcile: nie udało się wypisać wykonawców — pomijam", exc_info=True)
            return
        adopted: dict[str, str] = {}
        for running in managed:
            powod = self._powod_odrzucenia(running, adopted)
            if powod is not None:
                logger.warning(
                    "Reconcile: %s (%s, scope %r) — ubijam",
                    powod,
                    running.container_id[:12],
                    running.scope,
                )
                self._safe_remove(running.container_id)
                continue
            adopted[running.scope] = running.container_id
        if adopted:
            teraz = self._clock()
            with self._lock:
                for scope, container_id in adopted.items():
                    self._by_scope[scope] = _ManagedExecutor(scope, container_id, teraz)
        logger.info(
            "Reconcile: adoptowano %d wykonawców, ubito %d",
            len(adopted),
            len(managed) - len(adopted),
        )

    def _powod_odrzucenia(self, running: RunningExecutor, adopted: dict[str, str]) -> str | None:
        """Powód, dla którego zastanego kontenera NIE adoptujemy — albo ``None``, gdy wolno.

        Kolejność warunków jest od najtańszego do najbardziej pojemnego, ale nie to jest w niej
        istotne: każdy z nich opisuje INNY sposób, w jaki adopcja psuje coś po cichu, i każdy
        kończy się tak samo — usunięciem. Menedżer ma po reconcile trzymać wyłącznie kontenery,
        za które umie odpowiedzieć.
        """
        if not _SCOPE_RE.match(running.scope):
            return "sierota bez czytelnego scope"
        if not running.running:
            # Nie wykonawca, tylko ślad po awarii (np. ubity limitem pamięci). Adopcja wpisałaby
            # do rejestru trupa, a `ensure` tej rozmowy zwracałby ścieżkę gniazda, którego nikt
            # nie nasłuchuje — czyli awarię przesuniętą o jedno wywołanie dalej.
            return "kontener zatrzymany, nie wykonawca"
        if running.image != self._engine.image:
            return f"obraz {running.image!r} ≠ bieżący {self._engine.image!r}"
        if running.scope in adopted:
            # Dwa kontenery tej samej rozmowy: rejestr trzyma jeden wpis na scope, więc drugi
            # zniknąłby z pola widzenia menedżera i został na hoście bez właściciela.
            return "duplikat scope (drugi kontener tej samej rozmowy)"
        if len(adopted) >= self._max:
            # Limit N jest granicą zużycia hosta, a nie regułą samego `ensure`. Bez tego warunku
            # menedżer wstawał ponad limit i wyrównywał go dopiero eksmisją LRU — czyli kosztem
            # rozmowy, która akurat poprosiła o powłokę.
            return f"ponad limit N={self._max}"
        return None

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

    def _evict_for_room_locked(self, scope: str) -> list[_ManagedExecutor]:
        """Zrób miejsce na nowego wykonawcę: zdejmij LRU z rejestru, dopóki jest pod limitem.

        Zwraca ofiary do zgaszenia POZA zamkiem — sam wpis znika tu (pod zamkiem), ale gaszenie
        kontenera i sprzątanie gniazda robi ``_teardown`` wywołany przez ``ensure`` już bez zamka.
        „Zajętość" nie jest śledzona osobno (tury i tak są szeregowane wyżej — ADR 0010), więc
        LRU liczymy po ``last_used``. Eksmisja usuwa NAJDAWNIEJ używanego, aż zrobi się miejsce.
        """
        victims: list[_ManagedExecutor] = []
        while len(self._by_scope) >= self._max:
            victim = min(self._by_scope.values(), key=lambda e: e.last_used)
            del self._by_scope[victim.scope]
            victims.append(victim)
            logger.info(
                "Limit N=%d osiągnięty — eksmituję LRU scope %s pod %s",
                self._max,
                victim.scope,
                scope,
            )
        return victims

    def _teardown(self, executor: _ManagedExecutor) -> None:
        """Ubij kontener (poza zamkiem — wolne I/O) i sprzątnij gniazdo (pod zamkiem, gdy wolne)."""
        self._safe_remove(executor.container_id)
        self._cleanup_if_free(executor.scope)

    def _cleanup_if_free(self, scope: str) -> None:
        """Sprzątnij gniazdo scope'a — TYLKO, jeśli scope nie został w międzyczasie re-ensure'owany.

        To domyka wyścig reap↔ensure: reaper zdejmuje scope z rejestru pod zamkiem, ale zanim zdąży
        sprzątnąć gniazdo, nowe polecenie tej samej rozmowy może postawić świeżego wykonawcę na tym
        SAMYM gnieździe. Sprawdzenie „scope wciąż nieobecny" i samo sprzątanie biegną pod zamkiem, a
        ``ensure`` trzyma zamek przez cały ``prepare``/``run``/``wait_ready`` — więc albo sprzątamy,
        zanim ``ensure`` zdąży odtworzyć katalog, albo widzimy już zarejestrowanego nowego wykonawcę
        i gniazda NIE ruszamy (należy do żywego procesu). Samo sprzątanie to szybkie ``unlink``+
        ``rmdir``, więc trzymanie zamka na jego czas nie stalluje niczego.
        """
        with self._lock:
            if scope in self._by_scope:
                return
            try:
                self._workspace.cleanup(scope)
            except OSError:
                logger.warning("Nie udało się sprzątnąć gniazda scope %s", scope, exc_info=True)

    def _safe_remove(self, container_id: str) -> None:
        try:
            self._engine.remove(container_id)
        except Exception:  # noqa: BLE001 — usuwanie best-effort; brak kontenera to cel, nie błąd
            logger.warning("Nie udało się usunąć kontenera %s", container_id[:12], exc_info=True)


def _monotonic() -> float:
    import time

    return time.monotonic()
