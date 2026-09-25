"""``ScopeWorkspace`` na systemie plików (ADR infra 0012) — podkatalogi scope'a i gotowość gniazda.

Menedżer JEST właścicielem cyklu życia podkatalogu: tworzy go (idempotentnie) przed startem
wykonawcy,
a po reap sprząta jego gniazdo, zostawiając brudnopis. Gotowość = pojawienie się pliku gniazda w
podkatalogu scope'a. ``chown`` na uid 10001 jest best-effort (poza rootem odpuszcza), więc te sondy
biegną jako zwykły użytkownik.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from sufler.adapters.outbound.filesystem_scope_workspace import FilesystemScopeWorkspace

_SCOPE = f"teams-graph/{'a' * 32}"


def _workspace(tmp_path: Path) -> tuple[FilesystemScopeWorkspace, Path, Path]:
    scratch = tmp_path / "scratchpad"
    sock = tmp_path / "sock"
    scratch.mkdir()
    sock.mkdir()
    return FilesystemScopeWorkspace(scratch, sock), scratch, sock


def test_prepare_tworzy_oba_podkatalogi_idempotentnie(tmp_path: Path):
    """``prepare`` zakłada podkatalog brudnopisu i gniazda scope'a; drugie wywołanie nie wybucha."""
    workspace, scratch, sock = _workspace(tmp_path)

    workspace.prepare(_SCOPE)
    workspace.prepare(_SCOPE)  # idempotencja — kluczowa dla ciepłego wykonawcy

    assert (scratch / _SCOPE).is_dir()
    assert (sock / _SCOPE).is_dir()


def test_socket_path_jest_w_ukladzie_aplikacji(tmp_path: Path):
    """Ścieżka gniazda liczy się względem ``sock_root`` — tego samego po stronie aplikacji i
    menedżera."""
    workspace, _, sock = _workspace(tmp_path)

    assert workspace.socket_path(_SCOPE) == str(sock / _SCOPE / "exec.sock")


def test_wait_ready_czeka_na_pojawienie_sie_gniazda(tmp_path: Path):
    """Gotowość = plik gniazda w podkatalogu scope'a; pojawienie się w tle kończy oczekiwanie."""
    workspace, _, sock = _workspace(tmp_path)
    workspace.prepare(_SCOPE)
    socket_file = sock / _SCOPE / "exec.sock"

    def create_late() -> None:
        time.sleep(0.1)
        socket_file.write_bytes(b"")

    threading.Thread(target=create_late, daemon=True).start()
    assert workspace.wait_ready(_SCOPE, timeout_s=2.0) is True


def test_wait_ready_zwraca_false_po_oknie(tmp_path: Path):
    """Brak gniazda w oknie → ``False`` (menedżer wycofa start), nie zawieszenie."""
    workspace, _, _ = _workspace(tmp_path)
    workspace.prepare(_SCOPE)

    assert workspace.wait_ready(_SCOPE, timeout_s=0.2) is False


def test_cleanup_usuwa_gniazdo_ale_zostawia_brudnopis(tmp_path: Path):
    """Reap sprząta ulotne gniazdo; pliki rozmowy w brudnopisie ZOSTAJĄ (sprząta je prune_stale)."""
    workspace, scratch, sock = _workspace(tmp_path)
    workspace.prepare(_SCOPE)
    (sock / _SCOPE / "exec.sock").write_bytes(b"")
    (scratch / _SCOPE / "raport.md").write_bytes(b"tresc")

    workspace.cleanup(_SCOPE)

    assert not (sock / _SCOPE).exists()
    assert (scratch / _SCOPE / "raport.md").exists()  # brudnopis nietknięty


def test_prepare_odswieza_czas_modyfikacji_katalogu_rozmowy(tmp_path: Path):
    """Sprzątacz TTL mierzy aktywność rozmowy najnowszym mtime w katalogu, a w układzie
    „powłoka ON, workspace OFF" rozmowa potrafi być żywa i niczego nie zapisywać: `cat`, `ls`
    i `sufler-search` mtime nie ruszają, `mkdir(exist_ok=True)` też nie.

    Bez tego dotknięcia rozmowa używana codziennie, ale wyłącznie do czytania, traciłaby
    brudnopis po `retention_days` — kierunek pomyłki: utrata danych użytkownika.
    """
    import os
    import time

    scratch, sock = tmp_path / "scratch", tmp_path / "sock"
    workspace = FilesystemScopeWorkspace(scratch, sock)
    scope = "teams_graph/" + "a" * 32
    workspace.prepare(scope)
    dawno = time.time() - 60 * 24 * 3600
    os.utime(scratch / scope, (dawno, dawno))

    workspace.prepare(scope)

    assert (scratch / scope).stat().st_mtime > dawno + 24 * 3600
