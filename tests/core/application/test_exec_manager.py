"""Cykl życia menedżera wykonawców (ADR infra 0012) — czysta logika na atrapach.

Sercem menedżera są reguły, nie transport: idempotencja ``ensure``, limit N z eksmisją LRU, reap po
TTL, reconcile po etykiecie i ścisła walidacja scope'a. Wszystkie testują się bez demona Dockera i
bez
systemu plików — silnik i workspace to atrapy, a zegar wstrzykujemy, żeby sterować TTL/LRU bez
``sleep``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from workmate.core.application.exec_manager import ExecManagerService, validate_scope
from workmate.core.errors import ExecManagerError
from workmate.core.ports.exec_manager import ContainerSpec, RunningExecutor

_HASH = "a" * 32
_SCOPE = f"teams-graph/{_HASH}"
_SCOPE_B = f"teams-graph/{'b' * 32}"
# Obraz, z którego atrapa silnika „stawia" wykonawców — reconcile porównuje z nim to, co zastał.
_OBRAZ = "workmate:test"
_OBRAZ_STARY = "workmate:poprzedni"


def _zastany(container_id: str, scope: str, *, image: str = _OBRAZ, running: bool = True):
    """Kontener zastany przez reconcile po restarcie menedżera."""
    return RunningExecutor(container_id=container_id, scope=scope, image=image, running=running)


@dataclass
class FakeEngine:
    """Atrapa silnika: nadaje kolejne id, zapisuje wywołania, umie udać odmowę startu."""

    started: list[ContainerSpec] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    managed: list[RunningExecutor] = field(default_factory=list)
    fail_run: bool = False
    image: str = _OBRAZ
    _counter: int = 0

    def run(self, spec: ContainerSpec) -> str:
        if self.fail_run:
            raise ExecManagerError("silnik odmówił startu")
        self.started.append(spec)
        self._counter += 1
        return f"cid-{self._counter}"

    def remove(self, container_id: str) -> None:
        self.removed.append(container_id)

    def list_managed(self) -> list[RunningExecutor]:
        return list(self.managed)


@dataclass
class FakeWorkspace:
    """Atrapa systemu plików: zapisuje prepare/cleanup, oddaje sterowalną gotowość i ścieżkę
    gniazda."""

    ready: bool = True
    prepared: list[str] = field(default_factory=list)
    cleaned: list[str] = field(default_factory=list)

    def prepare(self, scope: str) -> None:
        self.prepared.append(scope)

    def wait_ready(self, scope: str, timeout_s: float) -> bool:
        return self.ready

    def socket_path(self, scope: str) -> str:
        return f"/sock/{scope}/exec.sock"

    def cleanup(self, scope: str) -> None:
        self.cleaned.append(scope)


class _Clock:
    """Ręcznie przesuwany zegar monotoniczny — testy sterują TTL/LRU bez czekania."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _service(
    engine: FakeEngine, workspace: FakeWorkspace, **kwargs
) -> tuple[ExecManagerService, _Clock]:
    clock = _Clock()
    service = ExecManagerService(engine, workspace, run_id="run-1", clock=clock, **kwargs)
    return service, clock


# --- walidacja scope ---------------------------------------------------------


@pytest.mark.parametrize(
    "scope",
    [
        "teams-graph/../secret",
        "teams-graph/short",
        f"TEAMS/{_HASH}",  # wielkie litery poza alfabetem kanału
        f"a/b/{_HASH}",  # dwa ukośniki
        f"teams graph/{_HASH}",  # spacja
        f"/{_HASH}",  # pusty kanał
        f"teams-graph/{_HASH}x",  # hash za długi
        f"teams-graph/{_HASH}\n",  # końcowy newline — ``$`` by go przepuścił, ``\Z`` nie
        "",
    ],
)
def test_scope_spoza_alfabetu_jest_odrzucany(scope: str):
    """Walidacja jest granicą: cokolwiek mogłoby wskazać podkatalog spoza brudnopisu, leci
    błędem."""
    with pytest.raises(ExecManagerError):
        validate_scope(scope)


def test_zly_scope_nie_dotyka_ani_silnika_ani_systemu_plikow():
    """Odmowa PRZED jakimkolwiek efektem ubocznym — zły scope nie dochodzi do Dockera ani
    ``mkdir``."""
    engine, workspace = FakeEngine(), FakeWorkspace()
    service, _ = _service(engine, workspace)

    with pytest.raises(ExecManagerError):
        service.ensure("teams-graph/../etc")

    assert engine.started == []
    assert workspace.prepared == []


# --- ensure: idempotencja, kolejność, gotowość -------------------------------


def test_ensure_stawia_wykonawce_i_zwraca_sciezke_gniazda():
    """Pierwszy ``ensure``: przygotuj podkatalog, postaw kontener, zwróć ścieżkę gniazda
    aplikacji."""
    engine, workspace = FakeEngine(), FakeWorkspace()
    service, _ = _service(engine, workspace)

    path = service.ensure(_SCOPE)

    assert path == f"/sock/{_SCOPE}/exec.sock"
    assert workspace.prepared == [_SCOPE]
    assert [spec.scope for spec in engine.started] == [_SCOPE]
    assert engine.started[0].subpath == _SCOPE
    assert engine.started[0].labels["workmate.exec.scope"] == _SCOPE


def test_ensure_jest_idempotentne_dla_cieplego_wykonawcy():
    """Drugie ``ensure`` tego samego scope'a NIE stawia drugiego kontenera — oddaje ciepły
    uchwyt."""
    engine, workspace = FakeEngine(), FakeWorkspace()
    service, _ = _service(engine, workspace)

    first = service.ensure(_SCOPE)
    second = service.ensure(_SCOPE)

    assert first == second
    assert len(engine.started) == 1
    assert workspace.prepared == [_SCOPE]


def test_brak_gotowosci_gniazda_wycofuje_start_i_podnosi_blad():
    """Wykonawca, który nie wystawił gniazda, jest ubijany i sprzątany, a ``ensure`` podnosi
    błąd."""
    engine, workspace = FakeEngine(), FakeWorkspace(ready=False)
    service, _ = _service(engine, workspace)

    with pytest.raises(ExecManagerError):
        service.ensure(_SCOPE)

    assert engine.removed == ["cid-1"]
    assert workspace.cleaned == [_SCOPE]
    # Po nieudanym starcie rejestr jest czysty — kolejny ``ensure`` spróbuje od nowa.
    workspace.ready = True
    service.ensure(_SCOPE)
    assert len(engine.started) == 2


# --- limit N + eksmisja LRU --------------------------------------------------


def test_limit_N_eksmituje_najdawniej_uzywanego():
    """Po osiągnięciu limitu nowy wykonawca wypycha LRU — kontener eksmitowanego jest ubijany."""
    engine, workspace = FakeEngine(), FakeWorkspace()
    service, clock = _service(engine, workspace, max_executors=2)

    service.ensure(_SCOPE)  # cid-1, last_used=1000
    clock.now = 1001.0
    service.ensure(_SCOPE_B)  # cid-2, last_used=1001
    clock.now = 1002.0
    scope_c = f"cli/{'c' * 32}"
    service.ensure(scope_c)  # przekracza limit → eksmisja LRU (_SCOPE, cid-1)

    assert "cid-1" in engine.removed
    assert workspace.cleaned == [_SCOPE]
    # _SCOPE wypadł — ponowny ``ensure`` stawia go od nowa (trzeci start tego scope'a byłby
    # czwartym).
    assert len(engine.started) == 3


def test_dotkniecie_cieplego_odswieza_LRU():
    """``ensure`` ciepłego odświeża ``last_used`` — to on NIE jest już najdawniej używany."""
    engine, workspace = FakeEngine(), FakeWorkspace()
    service, clock = _service(engine, workspace, max_executors=2)

    service.ensure(_SCOPE)  # last_used=1000
    clock.now = 1001.0
    service.ensure(_SCOPE_B)  # last_used=1001
    clock.now = 1002.0
    service.ensure(_SCOPE)  # dotknięcie _SCOPE → last_used=1002 (teraz LRU to _SCOPE_B)
    clock.now = 1003.0
    scope_c = f"cli/{'c' * 32}"
    service.ensure(scope_c)  # eksmisja LRU → _SCOPE_B, nie _SCOPE

    assert workspace.cleaned == [_SCOPE_B]


# --- reap po TTL -------------------------------------------------------------


def test_reap_idle_gasi_tylko_przeterminowanych():
    """Reap po TTL gasi wykonawcę bezczynnego dłużej niż okno; świeżego zostawia."""
    engine, workspace = FakeEngine(), FakeWorkspace()
    service, clock = _service(engine, workspace, idle_ttl_s=100.0)

    service.ensure(_SCOPE)  # last_used=1000
    clock.now = 1050.0
    service.ensure(_SCOPE_B)  # last_used=1050
    clock.now = 1101.0  # _SCOPE: 101s bezczynności (>100), _SCOPE_B: 51s (<100)

    killed = service.reap_idle()

    assert killed == 1
    assert workspace.cleaned == [_SCOPE]
    # Ubity scope stawia się od nowa; ciepły odpowiada bez nowego startu.
    assert len(service._by_scope) == 1  # noqa: SLF001 — sonda rejestru od środka


# --- reconcile po restarcie --------------------------------------------------


def test_reconcile_adoptuje_poprawne_a_ubija_sieroty():
    """Po restarcie: wykonawca z czytelnym scope jest adoptowany, bez scope — ubijany."""
    engine, workspace = FakeEngine(), FakeWorkspace()
    engine.managed = [
        _zastany("cid-alive", _SCOPE),
        _zastany("cid-orphan", "bez-scope"),
    ]
    service, _ = _service(engine, workspace)

    service.reconcile()

    assert engine.removed == ["cid-orphan"]
    # Adoptowany jest znany: ``ensure`` tego scope'a nie stawia nowego kontenera.
    service.ensure(_SCOPE)
    assert engine.started == []


def test_reconcile_ubija_wykonawce_z_POPRZEDNIEGO_obrazu():
    """Adopcja po podbiciu obrazu serwowałaby stary kod — i to bezterminowo.

    Regresja wprowadzona naprawą `list_managed`: dopóki wypisywanie rzucało, rejestr zostawał
    pusty i pierwszy `ensure` stawiał wykonawcę na nowo, więc zepsuty odzysk PRZYPADKIEM
    gwarantował poprawne wdrożenie. Po naprawie adoptowany kontener ze starego tagu żyje tak
    długo, jak długo rozmowa jest czynna: każde `ensure` odświeża `last_used`, więc TTL nie
    dobiega nigdy. Podbicie wygląda wtedy na udane, a powłoka jedzie na wydaniu sprzed niego.
    """
    engine, workspace = FakeEngine(), FakeWorkspace()
    engine.managed = [_zastany("cid-stary", _SCOPE, image=_OBRAZ_STARY)]
    service, _ = _service(engine, workspace)

    service.reconcile()

    assert engine.removed == ["cid-stary"]
    # Rozmowa dostaje wykonawcę z BIEŻĄCEGO obrazu, a nie adoptowanego poprzednika.
    service.ensure(_SCOPE)
    assert [spec.scope for spec in engine.started] == [_SCOPE]


def test_reconcile_ubija_zatrzymanego_zamiast_go_adoptowac():
    """Kontener `exited` (np. po limicie pamięci) to ślad po awarii, nie wykonawca.

    Adopcja wpisałaby do rejestru trupa, a `ensure` tej rozmowy zwracałby ścieżkę gniazda,
    którego nikt nie nasłuchuje — awaria przesunięta o jedno wywołanie dalej, już bez śladu
    po przyczynie.
    """
    engine, workspace = FakeEngine(), FakeWorkspace()
    engine.managed = [_zastany("cid-trup", _SCOPE, running=False)]
    service, _ = _service(engine, workspace)

    service.reconcile()

    assert engine.removed == ["cid-trup"]
    service.ensure(_SCOPE)
    assert [spec.scope for spec in engine.started] == [_SCOPE]


def test_reconcile_nie_wstaje_ponad_limit_N():
    """Limit N jest granicą zużycia hosta, więc obowiązuje też adopcję, nie tylko `ensure`.

    Bez tego menedżer wstawał z rejestrem większym niż limit i wyrównywał go dopiero eksmisją
    LRU — czyli kosztem rozmowy, która akurat poprosiła o powłokę.
    """
    engine, workspace = FakeEngine(), FakeWorkspace()
    engine.managed = [
        _zastany(f"cid-{i}", f"teams-graph/{format(i, 'x') * 32}") for i in range(1, 4)
    ]
    service, _ = _service(engine, workspace, max_executors=2)

    service.reconcile()

    assert engine.removed == ["cid-3"]
    assert len(service._by_scope) == 2  # noqa: SLF001 — sonda rejestru od środka


def test_reconcile_ubija_duplikat_scope():
    """Dwa kontenery jednej rozmowy: rejestr ma jeden wpis na scope, więc drugi zniknąłby z pola
    widzenia menedżera i został na hoście bez właściciela."""
    engine, workspace = FakeEngine(), FakeWorkspace()
    engine.managed = [_zastany("cid-pierwszy", _SCOPE), _zastany("cid-drugi", _SCOPE)]
    service, _ = _service(engine, workspace)

    service.reconcile()

    assert engine.removed == ["cid-drugi"]
    assert len(service._by_scope) == 1  # noqa: SLF001 — sonda rejestru od środka


def test_reap_jawny_gasi_konkretny_scope():
    """``reap(scope)`` ubija kontener i sprząta gniazdo; nieznany scope to no-op."""
    engine, workspace = FakeEngine(), FakeWorkspace()
    service, _ = _service(engine, workspace)
    service.ensure(_SCOPE)

    service.reap(_SCOPE)
    service.reap(_SCOPE_B)  # nieznany — bez efektu, bez wyjątku

    assert engine.removed == ["cid-1"]
    assert workspace.cleaned == [_SCOPE]


def test_teardown_nie_kasuje_gniazda_re_ensure_owanego_scope():
    """Wyścig reap↔ensure: reaper NIE może skasować gniazda wykonawcy, który w międzyczasie wrócił.

    Reaper zdejmuje scope z rejestru, ale zanim sprzątnie gniazdo, nowe polecenie tej samej rozmowy
    stawia świeżego wykonawcę na tym SAMYM gnieździe. Dokończony teardown starego wykonawcy musi
    wtedy zostawić gniazdo nowego w spokoju — inaczej rozmowa zostałaby zawieszona na trwałe (nowy
    wpis wygląda zdrowo, a gniazda nie ma). Odtwarzamy przeplot ręcznie, bo jest deterministyczny.
    """
    engine, workspace = FakeEngine(), FakeWorkspace()
    service, _ = _service(engine, workspace)
    service.ensure(_SCOPE)  # cid-1
    victim = service._by_scope[_SCOPE]  # noqa: SLF001 — odtwarzamy przeplot od środka

    # Reaper zdjął wpis pod zamkiem; nowe polecenie zdążyło re-ensure'ować scope (cid-2) ZANIM
    # reaper dokończył teardown starego wykonawcy.
    with service._lock:  # noqa: SLF001
        del service._by_scope[_SCOPE]
    service.ensure(_SCOPE)  # cid-2 — żywy wykonawca na tym samym gnieździe

    service._teardown(victim)  # noqa: SLF001 — dokończenie teardownu starego wykonawcy

    assert "cid-1" in engine.removed, "stary kontener ma zostać ubity"
    assert workspace.cleaned == [], "gniazdo ŻYWEGO nowego wykonawcy nie może zostać skasowane"
    assert service._by_scope[_SCOPE].container_id == "cid-2"  # noqa: SLF001
