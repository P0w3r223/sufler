"""Klient menedżera i runner per rozmowa (ADR infra 0012) — na PRAWDZIWYCH gniazdach unix.

Przedmiotem kontraktu jest to, co dzieje się MIĘDZY procesami: aplikacja pyta menedżera o gniazdo
wykonawcy scope'a, a dopiero potem wysyła tam polecenie. Atrapa gniazda sprawdzałaby własną
wierność,
więc klient jedzie na realnym serwerze kontrolnym, a runner — na realnym wykonawcy
(``exec_server``).

POSIX-only: gniazda unix nie istnieją na Windows, a cała warstwa wykonawcy biegnie w kontenerze.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fcntl", reason="menedżer wykonawców jest POSIX-only (gniazda unix)")

from workmate.adapters.inbound import exec_server  # noqa: E402
from workmate.adapters.outbound.exec_client import ManagedCommandRunner  # noqa: E402
from workmate.adapters.outbound.exec_manager_client import SocketExecManagerClient  # noqa: E402
from workmate.core.errors import ExecManagerError  # noqa: E402


def _serve_control(sock_path: Path, response: dict) -> threading.Thread:
    """Wystaw jednorazowy serwer kontrolny oddający ``response`` na pierwsze żądanie."""

    def loop() -> None:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock_path))
        server.listen(1)
        conn, _ = server.accept()
        with conn, conn.makefile("rwb") as stream:
            stream.readline()
            stream.write(json.dumps(response).encode("utf-8") + b"\n")
            stream.flush()
        server.close()

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    for _ in range(200):
        if sock_path.exists():
            break
        time.sleep(0.01)
    return thread


def test_ensure_zwraca_sciezke_gniazda(tmp_path: Path):
    """Ścieżka szczęśliwa: menedżer oddaje ``socket_path``, klient go zwraca."""
    control = tmp_path / "control.sock"
    _serve_control(control, {"socket_path": "/sock/scope/exec.sock"})

    result = SocketExecManagerClient(control).ensure("teams-graph/x")

    assert result == "/sock/scope/exec.sock"


def test_odmowa_menedzera_wraca_jako_exec_manager_error(tmp_path: Path):
    """Odpowiedź ``{"error": ...}`` staje się ``ExecManagerError`` — warstwa Bash ją degraduje."""
    control = tmp_path / "control.sock"
    _serve_control(control, {"error": "scope niepoprawny"})

    with pytest.raises(ExecManagerError, match="niepoprawny"):
        SocketExecManagerClient(control).ensure("zly")


def test_brak_menedzera_wraca_jako_exec_manager_error(tmp_path: Path):
    """Brak gniazda kontrolnego to błąd menedżera, nie wyjątek transportu lecący wyżej."""
    with pytest.raises(ExecManagerError, match="niedostępny"):
        SocketExecManagerClient(tmp_path / "nie-ma.sock").ensure("teams-graph/x")


# --- ManagedCommandRunner: ensure → wykonawca --------------------------------


class _FakeManager:
    """Atrapa ``ExecManager``: oddaje zadaną ścieżkę gniazda albo podnosi błąd."""

    def __init__(self, socket_path: str | None = None, *, boom: bool = False) -> None:
        self._socket_path = socket_path
        self._boom = boom
        self.calls: list[str] = []

    def ensure(self, scope: str) -> str:
        self.calls.append(scope)
        if self._boom:
            raise ExecManagerError("menedżer niedostępny")
        assert self._socket_path is not None
        return self._socket_path


def _wait_connectable(sock_path: Path) -> None:
    """Czekaj, aż gniazdo NASŁUCHUJE — sam plik istnieje już po ``bind``, przed ``listen``."""
    for _ in range(200):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.5)
                probe.connect(str(sock_path))
            return
        except OSError:
            time.sleep(0.01)
    raise AssertionError("gniazdo wykonawcy nie zaczęło nasłuchiwać")


def test_runner_pyta_menedzera_a_potem_wykonuje_polecenie(tmp_path: Path):
    """Runner rozwiązuje gniazdo przez ``ensure(scope)`` i dopiero wtedy wysyła polecenie."""
    exec_sock = tmp_path / "exec.sock"
    thread = threading.Thread(target=exec_server.serve, args=(exec_sock,), daemon=True)
    thread.start()
    _wait_connectable(exec_sock)

    manager = _FakeManager(str(exec_sock))
    runner = ManagedCommandRunner(manager, "teams-graph/scope")

    result = runner.run("echo zdalne", cwd=str(tmp_path))

    assert manager.calls == ["teams-graph/scope"]
    assert result.exit_code == 0
    assert result.stdout.strip() == "zdalne"


def test_niedostepny_menedzer_degraduje_do_wyniku_nie_wyjatku():
    """Odmowa menedżera wraca jako ``CommandResult`` z kodem -1 — tura agenta się nie wywraca."""
    manager = _FakeManager(boom=True)

    result = ManagedCommandRunner(manager, "teams-graph/scope").run("echo x")

    assert result.exit_code == -1
    assert "niedostępny" in result.stderr
