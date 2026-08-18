"""Wykonawca (``exec_server``): granice limitu polecenia i limit oczekiwania na połączeniu.

POSIX-only: moduł wykonawcy podnosi ``ImportError`` na Windows (gniazda unix, grupy procesów),
więc jego import wywróciłby ZBIERANIE całego pakietu testów. Bramka stoi PRZED importem i pyta
o ``fcntl`` — moduł, którego na Windows fizycznie nie ma — dokładnie jak w
``test_exec_manager_server.py``.
"""

from __future__ import annotations

import socket

import pytest

pytest.importorskip("fcntl", reason="wykonawca jest POSIX-only (gniazda unix, grupy procesów)")

from workmate.adapters.inbound import exec_server  # noqa: E402


def test_negative_timeout_from_the_model_does_not_kill_the_command_instantly():
    """Regresja: ``timeout_s`` układa MODEL, a ``min(t, _MAX)`` przepuszczał wartość ujemną.

    ``communicate(timeout=-5)`` zgłasza ``TimeoutExpired`` natychmiast, więc każde polecenie
    ginęło od razu — z wynikiem nieodróżnialnym od realnego przekroczenia czasu, czyli w trybie
    awarii, którego model nie umie zdiagnozować.
    """
    wynik = exec_server.run_command("echo ok", timeout_s=-5)

    assert wynik["timed_out"] is False
    assert wynik["exit_code"] == 0
    assert "ok" in str(wynik["stdout"])


def test_timeout_is_still_capped_from_above():
    """Podłoga nie może zdjąć sufitu — oba ograniczenia obowiązują naraz."""
    assert exec_server._MIN_TIMEOUT_S < exec_server._MAX_TIMEOUT_S
    assert exec_server.run_command("echo ok", timeout_s=10_000)["exit_code"] == 0


def test_silent_client_does_not_hold_the_worker_thread_forever(monkeypatch):
    """Regresja: gniazdo zaakceptowane NIE dziedziczy timeoutu nasłuchu.

    Klient, który się połączy i zamilknie, trzymał wątek i deskryptor do końca życia procesu —
    a wątków przybywa tu po jednym na połączenie. Bliźniaczy ``exec_manager_server`` miał ten
    limit od początku; wykonawca nie miał go wcale.
    """
    monkeypatch.setattr(exec_server, "_CONN_TIMEOUT_S", 0.05)
    lewy, prawy = socket.socketpair()
    try:
        exec_server._handle(prawy)  # klient (``lewy``) nie wysyła ani jednej linii
    finally:
        lewy.close()
    # Brak zawieszenia JEST wynikiem; obsługa kończy się cicho (zerwanie/timeout w logu).
    assert prawy.fileno() == -1  # gniazdo zamknięte przez ``with conn``
