"""Sondy startu drzwi teams-graph: które trwałe ścieżki muszą być zapisywalne, a które nie.

Bez sieci i bez Graph: token provider i pierwszy krok składania katalogu podmieniamy, więc
``main`` dochodzi dokładnie do bloku sond zapisywalności i tam się zatrzymuje.

Dwa poziomy, bo szew i zachowanie psują się osobno. Sondy „szwu" podmieniają
``require_writable`` na szpiega i pilnują, że drzwi w ogóle pytają ``Settings.persistent_paths``
(treść listy zamraża ``tests/test_config.py``). Sondy „zachowania" używają PRAWDZIWEGO
``require_writable`` i pilnują tego, czego szpieg z definicji nie zobaczy: czy drzwi WSTAJĄ,
gdy ścieżka jest niezapisywalna, a zdolność, która by z niej korzystała, jest wyłączona.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from workmate.adapters.inbound.teams_graph import app


class _Stop(Exception):
    """Przerwanie ``main`` zaraz za blokiem sond — dalej jest już sieć."""


def _base_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Minimalne env drzwi: wszystkie trwałe ścieżki w ``tmp_path`` (czyli zapisywalne)."""
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_WATCH", "team-1:channel-1")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_TOKEN_CACHE", str(tmp_path / "token.json"))
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_CLIENT_ID", "app-1")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_TENANT_ID", "tenant-1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("WORKMATE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WORKMATE_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.setenv("WORKMATE_CONVERSATIONS_DB", str(tmp_path / "conv.db"))
    # Brudnopis MUSI wskazywać ``tmp_path``. Odkąd sprzątacz TTL biegnie bezwarunkowo (nie za
    # bramką narzędzi), jego brak oznaczał wartość domyślną ``~/.workmate/workspace`` — czyli
    # pakiet testów kasujący katalogi w REALNYM katalogu domowym dewelopera. Kierunek pomyłki
    # najgorszy z możliwych: cicho, nieodwracalnie i poza ``tmp_path``.
    monkeypatch.setenv("WORKMATE_WORKSPACE_DIR", str(tmp_path / "scratchpad"))
    monkeypatch.setattr(app, "build_token_provider", lambda _s: object(), raising=False)
    monkeypatch.setattr(
        "workmate.adapters.inbound.teams_graph.auth.build_token_provider", lambda _s: object()
    )

    def _stop(*_args: Any, **_kwargs: Any) -> None:
        raise _Stop

    monkeypatch.setattr(app, "_build_bridge_catalog", _stop)


def _enable_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Włącz mutację notatek ze WSZYSTKIMI jej warunkami koniecznymi (``Settings.validate``).

    Mutacja wymaga mapy tożsamości (nadawca musi być rozpoznany) i narzędzia ``File`` (to jego
    akcja) — bez nich start pada na walidacji konfiguracji, czyli PRZED blokiem sond, i sonda
    mierzyłaby co innego, niż deklaruje.
    """
    mapa = tmp_path / "identities.yaml"
    mapa.write_text("people: []\n", encoding="utf-8")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_IDENTITIES", str(mapa))
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_ENABLE_FILE_TOOL", "true")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_MUTATION", "true")


def _unwritable_dir(tmp_path: Path) -> Path:
    """Katalog, którego NIE DA SIĘ utworzyć — jego przodek jest plikiem.

    Odwzorowuje montaż ``:ro`` floty w sposób przenośny: bity uprawnień na Windows nie działają
    jak na POSIX, a ``mkdir`` pod plikiem zawodzi wszędzie tak samo (``require_writable``
    zamienia ``OSError`` na ``ValueError``). Ten sam chwyt stoi w ``tests/test_config.py``.
    """
    blokada = tmp_path / "montaz-ro"
    blokada.write_text("to plik, nie katalog", encoding="ascii")
    return blokada / "snapshots" / "notes"


# --- szew: drzwi pytają ``Settings.persistent_paths`` --------------------------


@pytest.fixture
def srodowisko(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, str, bool]]:
    """Env drzwi + szpieg na ``require_writable``; zwraca listę nagranych wywołań."""
    _base_env(tmp_path, monkeypatch)
    monkeypatch.setenv("WORKMATE_NOTE_SNAPSHOTS_DIR", str(tmp_path / "snapshots"))
    monkeypatch.setenv("WORKMATE_METRICS_DB", str(tmp_path / "metrics.db"))
    monkeypatch.setenv("WORKMATE_AUDIT_DB", str(tmp_path / "audit.db"))
    _enable_mutation(tmp_path, monkeypatch)

    nagrane: list[tuple[Path, str, bool]] = []

    def _spy(path: Path, env_var: str, *, is_directory: bool = False) -> None:
        nagrane.append((path, env_var, is_directory))

    monkeypatch.setattr(app, "require_writable", _spy)
    return nagrane


def test_startup_probes_every_persistent_core_path(srodowisko):
    """Regresja: ``note_snapshots_dir`` (ADR 0065) nie miał sondy, w odróżnieniu od reszty.

    Domyślnie ląduje pod montażem read-only floty, więc pierwsza mutacja notatki padała dopiero
    przy użyciu — a rejestrator audytu łyka błędy per wywołanie, czyli po cichu.
    """
    with pytest.raises(_Stop):
        app.main()

    sondowane = {env_var for _p, env_var, _d in srodowisko}
    assert "WORKMATE_NOTE_SNAPSHOTS_DIR" in sondowane
    assert "WORKMATE_METRICS_DB" in sondowane
    assert "WORKMATE_AUDIT_DB" in sondowane
    # Sondy sprzed audytu zostają — sonda ma DOKŁADAĆ, nie podmieniać.
    assert {"WORKMATE_TEAMS_GRAPH_STATE", "WORKMATE_EVENTS_DB"} <= sondowane


def test_snapshots_dir_is_probed_as_a_directory_not_as_a_file_in_it(srodowisko):
    """Migawki to KATALOG docelowy — sonda o poziom wyżej przepuściłaby montaż read-only."""
    with pytest.raises(_Stop):
        app.main()

    wpis = next(w for w in srodowisko if w[1] == "WORKMATE_NOTE_SNAPSHOTS_DIR")
    assert wpis[2] is True


# --- szew: brudnopis nie może OBEJMOWAĆ trwałej ścieżki (sprzątacz TTL, ADR 0065) ---


def test_brudnopis_nad_migawkami_wywala_start_drzwi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Regresja: brudnopis wskazujący KORZEŃ wolumenu stanu przechodził walidację, a sprzątacz
    TTL kasował potem ``snapshots/notes`` przy każdym starcie drzwi — czyli migawki sprzed
    mutacji notatek, jedyną odwracalność z ADR 0065 działającą w ciągu doby.

    Sonda jedzie przez ``main``, nie przez samo ``WorkspaceSettings.validate``, bo bramka działa
    wyłącznie wtedy, gdy drzwi karmią ją listą z ``Settings.persistent_paths``. Pominięcie tego
    argumentu zostawiłoby sondy jednostkowe zielone, a produkcję bez ochrony.
    """
    _base_env(tmp_path, monkeypatch)
    wolumen_stanu = tmp_path / "state"
    monkeypatch.setenv("WORKMATE_NOTE_SNAPSHOTS_DIR", str(wolumen_stanu / "snapshots" / "notes"))
    # Pomyłka operatora: zgubiony segment ``/workspace`` na końcu ścieżki.
    monkeypatch.setenv("WORKMATE_WORKSPACE_DIR", str(wolumen_stanu))

    with pytest.raises(ValueError, match="WORKMATE_NOTE_SNAPSHOTS_DIR"):
        app.main()


def test_brudnopis_obok_migawek_nie_blokuje_startu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Kontrast do sondy wyżej: układ floty (rodzeństwo na wolumenie stanu) ma dojść dalej."""
    _base_env(tmp_path, monkeypatch)
    wolumen_stanu = tmp_path / "state"
    monkeypatch.setenv("WORKMATE_NOTE_SNAPSHOTS_DIR", str(wolumen_stanu / "snapshots" / "notes"))
    monkeypatch.setenv("WORKMATE_WORKSPACE_DIR", str(wolumen_stanu / "workspace"))

    with pytest.raises(_Stop):
        app.main()


# --- zachowanie: PRAWDZIWY ``require_writable`` na niezapisywalnej ścieżce ------


def test_door_starts_when_snapshots_are_unwritable_and_mutation_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Regresja: bezwarunkowa sonda migawek kładła drzwi na SZABLONIE DOMYŚLNYM.

    ``note_snapshots_dir`` domyślnie to ``data_dir/snapshots/notes``, a ``data`` jest na flocie
    montowane ``:ro``; ``deploy/docker/env.example`` nigdzie tego nie przekierowuje. Sonda bez
    warunku podnosiła więc ``ValueError``, którego ``main`` nie łapie — restart-loop przy
    WYŁĄCZONEJ mutacji notatek, czyli na katalogu, którego proces nigdy nie tknie.

    Szpieg tego nie zobaczy z definicji (nie wywraca się), więc tu leci PRAWDZIWA sonda.
    """
    _base_env(tmp_path, monkeypatch)
    monkeypatch.setenv("WORKMATE_NOTE_SNAPSHOTS_DIR", str(_unwritable_dir(tmp_path)))
    monkeypatch.delenv("WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_MUTATION", raising=False)

    # Dojście do ``_build_bridge_catalog`` JEST wynikiem: blok sond przepuścił drzwi dalej.
    with pytest.raises(_Stop):
        app.main()


def test_door_refuses_to_start_when_snapshots_are_unwritable_and_mutation_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Z włączoną mutacją migawka jest ścieżką KRYTYCZNĄ — brak zapisu ma zatrzymać start.

    Inaczej pierwsza zmiana notatki padłaby na zapisie migawki, czyli po tym, jak nadawca
    dostał potwierdzenie przyjęcia — dokładnie w trybie awarii, który sonda ma likwidować.
    """
    _base_env(tmp_path, monkeypatch)
    monkeypatch.setenv("WORKMATE_NOTE_SNAPSHOTS_DIR", str(_unwritable_dir(tmp_path)))
    _enable_mutation(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="nie jest zapisywalna"):
        app.main()


def test_optional_databases_stay_unconditional(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Metryki i audyt warunku NIE dostają: są na liście tylko, gdy operator wskazał plik.

    Wskazanie pliku jest już deklaracją „chcę tu pisać", więc niezapisywalna ścieżka to błąd
    konfiguracji, a nie zdolność wyłączona — i ma zatrzymać start, mimo wyłączonej mutacji.
    """
    _base_env(tmp_path, monkeypatch)
    monkeypatch.setenv("WORKMATE_NOTE_SNAPSHOTS_DIR", str(tmp_path / "snapshots"))
    monkeypatch.delenv("WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_MUTATION", raising=False)
    blokada = tmp_path / "plik-nie-katalog"
    blokada.write_text("x", encoding="ascii")
    monkeypatch.setenv("WORKMATE_AUDIT_DB", str(blokada / "audit.db"))

    with pytest.raises(ValueError, match="nie jest zapisywalna"):
        app.main()


# --- Sprzątanie brudnopisu nie wisi na bramce NARZĘDZI (znalezisko 9.13) ----------------


def _stary_katalog_rozmowy(brudnopis: Path) -> Path:
    """Katalog rozmowy z aktywnością sprzed 60 dni — dwukrotnie ponad TTL (30 dni)."""
    import os
    import time

    rozmowa = brudnopis / "teams_graph" / ("a" * 32)
    rozmowa.mkdir(parents=True)
    (rozmowa / "notatka.md").write_text("stara treść", encoding="utf-8")
    dawno = time.time() - 60 * 24 * 3600
    os.utime(rozmowa / "notatka.md", (dawno, dawno))
    os.utime(rozmowa, (dawno, dawno))
    return rozmowa


def test_stale_scratch_is_pruned_even_with_the_workspace_tools_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Sonda przeciw kodowi SPRZED poprawki: sprzątacz biegł tylko przy
    ``WORKMATE_ENABLE_WORKSPACE``, czyli przy bramce NARZĘDZI.

    W układzie docelowym („powłoka ON, workspace OFF") do brudnopisu pisze WYKONAWCA, a bramka
    narzędzi jest zamknięta — więc sprzątacz nie biegł ani razu, mimo TTL 30 dni. Retencja jest
    własnością danych, nie tego, które narzędzia są włączone.
    """
    _base_env(tmp_path, monkeypatch)
    brudnopis = tmp_path / "scratchpad"
    monkeypatch.setenv("WORKMATE_WORKSPACE_DIR", str(brudnopis))
    monkeypatch.delenv("WORKMATE_ENABLE_WORKSPACE", raising=False)
    rozmowa = _stary_katalog_rozmowy(brudnopis)

    with pytest.raises(_Stop):
        app.main()

    assert not rozmowa.exists()


def test_a_fresh_conversation_dir_survives_the_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Kontrast: sprzątacz bezwarunkowy nie może kasować katalogów, które ŻYJĄ — inaczej
    zamiast retencji byłaby utrata brudnopisu przy każdym restarcie drzwi."""
    _base_env(tmp_path, monkeypatch)
    brudnopis = tmp_path / "scratchpad"
    monkeypatch.setenv("WORKMATE_WORKSPACE_DIR", str(brudnopis))
    monkeypatch.delenv("WORKMATE_ENABLE_WORKSPACE", raising=False)
    swieza = brudnopis / "teams_graph" / ("b" * 32)
    swieza.mkdir(parents=True)
    (swieza / "notatka.md").write_text("świeża treść", encoding="utf-8")

    with pytest.raises(_Stop):
        app.main()

    assert swieza.exists()


def test_a_missing_scratch_root_is_not_a_startup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Bezwarunkowe wołanie nie może wywrócić drzwi tam, gdzie brudnopisu nie ma wcale —
    a to jest domyślny stan drzwi bez wolumenu scratchpada."""
    _base_env(tmp_path, monkeypatch)
    monkeypatch.setenv("WORKMATE_WORKSPACE_DIR", str(tmp_path / "nie-ma-mnie"))
    monkeypatch.delenv("WORKMATE_ENABLE_WORKSPACE", raising=False)

    with pytest.raises(_Stop):
        app.main()
