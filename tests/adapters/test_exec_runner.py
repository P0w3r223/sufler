"""Wykonawca poleceń (ADR 0057): protokół gniazda, limity i degradacja.

Testy jadą na PRAWDZIWYM gnieździe unix i prawdziwych procesach, nie na atrapach: przedmiotem
kontraktu jest właśnie to, co dzieje się MIĘDZY procesami (ucięcie wyjścia, timeout z zabiciem
grupy, zerwane połączenie). Atrapa gniazda sprawdzałaby wyłącznie własną wierność.

POSIX-only — gniazda unix i grupy procesów nie istnieją na Windows, a wykonawca z definicji
biegnie w kontenerze. Na maszynie deweloperskiej testy się pomijają; w obrazie (etap `test`
Dockerfile'a) biegną na tym Pythonie i tej architekturze, na której pojedzie produkcja.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fcntl", reason="wykonawca jest POSIX-only (gniazda unix, grupy procesów)")

from workmate.adapters.inbound import exec_server  # noqa: E402
from workmate.adapters.outbound.exec_client import SocketCommandRunner  # noqa: E402


@pytest.fixture
def runner(tmp_path: Path):
    """Wystaw wykonawcę na gnieździe w ``tmp_path`` i oddaj klienta wpiętego w to gniazdo."""
    sock_path = tmp_path / "exec.sock"
    thread = threading.Thread(target=exec_server.serve, args=(sock_path,), daemon=True)
    thread.start()
    for _ in range(200):  # gniazdo powstaje asynchronicznie — czekamy na jego pojawienie się
        if sock_path.exists():
            break
        time.sleep(0.01)
    assert sock_path.exists(), "wykonawca nie wystawił gniazda"
    yield SocketCommandRunner(sock_path)


def test_runs_command_and_returns_streams(runner: SocketCommandRunner, tmp_path: Path):
    """Ścieżka szczęśliwa: kod wyjścia, stdout i stderr wracają rozdzielone."""
    result = runner.run("echo ala; echo blad >&2", cwd=str(tmp_path))

    assert result.exit_code == 0
    assert result.stdout.strip() == "ala"
    assert result.stderr.strip() == "blad"
    assert result.truncated is False
    assert result.timed_out is False


def test_nonzero_exit_is_a_result_not_an_error(runner: SocketCommandRunner, tmp_path: Path):
    """Porażka polecenia to WYNIK — model poprawia się w kolejnej turze, tura się nie wywraca."""
    result = runner.run("exit 3", cwd=str(tmp_path))

    assert result.exit_code == 3


def test_output_is_truncated_and_flagged(runner: SocketCommandRunner, tmp_path: Path):
    """Wyjście ponad sufit jest przycięte i OZNACZONE — inaczej model uzna fragment za całość."""
    result = runner.run("head -c 200000 /dev/zero | tr '\\0' 'x'", cwd=str(tmp_path))

    assert result.truncated is True
    assert len(result.stdout.encode()) <= exec_server._MAX_OUTPUT_BYTES


def test_timeout_kills_process_group_and_flags_result(runner: SocketCommandRunner, tmp_path: Path):
    """Timeout zabija CAŁĄ grupę: potomek w tle nie przeżywa zabicia powłoki."""
    marker = tmp_path / "przezyl.txt"
    started = time.monotonic()
    result = runner.run(f"(sleep 3; echo x > {marker}) & sleep 30", cwd=str(tmp_path), timeout_s=1)
    elapsed = time.monotonic() - started

    assert result.timed_out is True
    assert elapsed < 10, "timeout nie przerwał polecenia"
    time.sleep(4)  # gdyby potomek przeżył, zdążyłby utworzyć marker
    assert not marker.exists(), "potomek przeżył timeout — grupa procesów nie została zabita"


def test_cwd_outside_workspace_falls_back_to_default(runner: SocketCommandRunner):
    """Nieistniejący katalog roboczy degraduje do domyślnego zamiast wywracać polecenie."""
    result = runner.run("pwd", cwd="/nie/ma/takiej/sciezki")

    assert result.exit_code == 0
    assert result.stdout.strip()


def test_missing_executor_degrades_to_result(tmp_path: Path):
    """Brak wykonawcy to wynik z niezerowym kodem, nie wyjątek lecący przez pętlę agenta."""
    result = SocketCommandRunner(tmp_path / "nie-ma.sock").run("echo x")

    assert result.exit_code == -1
    assert "niedostępny" in result.stderr


def test_malformed_request_gets_a_response_not_silence(runner: SocketCommandRunner, tmp_path: Path):
    """Zepsute żądanie dostaje odpowiedź — cisza wyglądałaby dla klienta jak zawieszenie."""
    sock_path = Path(runner._socket_path)  # noqa: SLF001 — test protokołu, celowo od środka
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(10)
        sock.connect(str(sock_path))
        sock.sendall(b"to nie jest json\n")
        with sock.makefile("rb") as stream:
            line = stream.readline()

    assert line, "wykonawca zamknął połączenie bez odpowiedzi"
    assert b"odrzuci" in line


def test_concurrent_commands_do_not_interleave(runner: SocketCommandRunner, tmp_path: Path):
    """Dwa polecenia naraz dostają swoje wyniki — wątek na połączenie, bez wspólnego stanu."""
    results: dict[str, str] = {}

    def call(tag: str) -> None:
        results[tag] = runner.run(f"echo {tag}", cwd=str(tmp_path)).stdout.strip()

    threads = [threading.Thread(target=call, args=(t,)) for t in ("pierwszy", "drugi", "trzeci")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert results == {"pierwszy": "pierwszy", "drugi": "drugi", "trzeci": "trzeci"}
