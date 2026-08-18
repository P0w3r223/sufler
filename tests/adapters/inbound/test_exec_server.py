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


# --- Granice zużycia polecenia (ADR infra 0013) ----------------------------------------
# Sondy biegną na PRAWDZIWEJ powłoce, bo mierzona własność jest własnością jądra, nie kodu:
# atrapa `subprocess` potwierdziłaby wyłącznie, że ułożyliśmy napis, którego nikt nie wykonał.


def test_a_command_writing_past_the_file_ceiling_is_stopped(tmp_path):
    """Kryterium odbioru Fazy 2: `dd … count=100000` kończy się BŁĘDEM, a nie pełnym dyskiem.

    Sprawdzamy dwie rzeczy naraz, bo obie są tą samą obietnicą: polecenie ginie, a plik na dysku
    urywa się DOKŁADNIE na sufitcie — nie „gdzieś w okolicy".
    """
    cel = tmp_path / "duzy.bin"
    ile_mb = exec_server._MAX_FILE_MB + 5

    wynik = exec_server.run_command(
        f"dd if=/dev/zero of={cel} bs=1M count={ile_mb} 2>/dev/null", cwd=str(tmp_path)
    )

    assert wynik["exit_code"] != 0
    assert cel.stat().st_size == exec_server._MAX_FILE_MB * 1024 * 1024


def test_the_model_gets_a_sentence_not_a_signal_number(tmp_path):
    """Granica, której model nie rozumie, wygląda jak defekt narzędzia — i skłania do obchodzenia
    jej kolejnymi próbami zamiast do zmiany podejścia. Surowe `-25` nie mówi nic."""
    cel = tmp_path / "duzy.bin"
    ile_mb = exec_server._MAX_FILE_MB + 5

    wynik = exec_server.run_command(
        f"dd if=/dev/zero of={cel} bs=1M count={ile_mb} 2>/dev/null", cwd=str(tmp_path)
    )

    assert "limit rozmiaru pliku" in str(wynik["stderr"])


def test_a_normal_command_is_untouched_by_the_prologue(tmp_path):
    """Prolog limitów nie może zmienić zachowania zwykłego polecenia — ani jego wyjścia,
    ani kodu wyjścia, ani katalogu, w którym startuje."""
    wynik = exec_server.run_command("pwd; echo tekst; exit 3", cwd=str(tmp_path))

    assert wynik["exit_code"] == 3
    assert str(tmp_path) in str(wynik["stdout"])
    assert "tekst" in str(wynik["stdout"])


def test_quoting_in_the_command_cannot_reach_the_prologue(tmp_path):
    """Polecenie jedzie jako ``$0``, więc zewnętrzna powłoka nigdy go nie parsuje.

    Gdyby doklejać je do treści prologu, ten napis rozerwałby cytowanie i uruchomił własne
    polecenie PRZED limitami — czyli dokładnie obszedł granicę, którą prolog zakłada.
    """
    wynik = exec_server.run_command("echo '\" ; echo WSTRZYKNIETE ; \"'", cwd=str(tmp_path))

    assert "WSTRZYKNIETE" not in str(wynik["stdout"]).replace('" ; echo WSTRZYKNIETE ; "', "")
    assert wynik["exit_code"] == 0


def test_both_shapes_of_a_signalled_exit_are_recognised():
    """Regresja: pierwsza wersja znała tylko kod UJEMNY.

    Powłoka melduje śmierć dziecka konwencją ``128 + N`` zawsze, gdy polecenie rozwidliła —
    czyli przy potoku, przekierowaniu i kilku poleceniach naraz. To najczęstszy kształt
    polecenia piszącego duży plik, więc luka milczała dokładnie tam, gdzie boli.
    """
    assert exec_server._sygnal_z_kodu(-exec_server.signal.SIGXFSZ) == exec_server.signal.SIGXFSZ
    assert exec_server._sygnal_z_kodu(128 + exec_server.signal.SIGXFSZ) == (
        exec_server.signal.SIGXFSZ
    )
    assert exec_server._sygnal_z_kodu(0) is None
    assert exec_server._sygnal_z_kodu(3) is None


def test_a_direct_command_killed_by_the_ceiling_also_explains_itself(tmp_path):
    """Ta sama obietnica na drugiej ścieżce: polecenie POJEDYNCZE ginie z kodem ujemnym."""
    cel = tmp_path / "duzy.bin"

    wynik = exec_server.run_command(
        f"dd if=/dev/zero of={cel} bs=1M count={exec_server._MAX_FILE_MB + 5}", cwd=str(tmp_path)
    )

    assert "limit rozmiaru pliku" in str(wynik["stderr"])
