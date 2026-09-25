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

from sufler.adapters.inbound import exec_server  # noqa: E402


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


def test_a_kill_from_outside_is_explained_but_a_timeout_is_not(tmp_path):
    """Ten sam sygnał, dwa różne fakty — i tylko jeden wymaga zdania.

    `SIGKILL` bez przekroczenia czasu ściennego znaczy „zdjął to ktoś z zewnątrz" (na tej flocie
    prawie zawsze zabójca OOM cgroupy). `SIGKILL` PO timeoucie to zachowanie zamierzone, które
    niesie już własne pole w wyniku — drugi komunikat o tym samym kazałby modelowi zgadywać,
    która przyczyna jest prawdziwa.
    """
    po_timeoucie = exec_server.run_command("sleep 30", cwd=str(tmp_path), timeout_s=1)

    assert po_timeoucie["timed_out"] is True
    assert "zdjęte z zewnątrz" not in str(po_timeoucie["stderr"])

    # Zabicie z zewnątrz odtwarzamy tak, jak robi to jądro: polecenie ubija samo siebie SIGKILL-em,
    # nie przekraczając limitu czasu. Prawdziwego OOM-a nie da się wywołać bez cgroupy z limitem.
    zdjete = exec_server.run_command("kill -9 $$", cwd=str(tmp_path), timeout_s=30)

    assert zdjete["timed_out"] is False
    assert "zdjęte z zewnątrz" in str(zdjete["stderr"])


def test_the_explanation_fits_inside_the_output_ceiling(tmp_path):
    """Sufit wyjścia ma obejmować CAŁE pole, a nie treść sprzed doklejenia zdania.

    Wyjście wraca do kontekstu i jest odsyłane w każdej kolejnej turze, więc przekroczenie sufitu
    kosztuje do końca rozmowy — a doklejone zdanie jest tą częścią, o której łatwo zapomnieć.
    """
    cel = tmp_path / "duzy.bin"
    # Wyjście generuje POLECENIE, nie test: 200 kB w treści polecenia przekracza limit argv.
    wynik = exec_server.run_command(
        "yes x | head -c 200000 >&2; "
        f"dd if=/dev/zero of={cel} bs=1M count={exec_server._MAX_FILE_MB + 5} 2>/dev/null",
        cwd=str(tmp_path),
    )

    assert "limit rozmiaru pliku" in str(wynik["stderr"])
    assert len(str(wynik["stderr"]).encode("utf-8")) <= exec_server._MAX_OUTPUT_BYTES


def test_the_file_ceiling_message_warns_that_the_file_is_truncated_on_disk(tmp_path):
    """Po SIGXFSZ na dysku zostaje plik o rozmiarze dokładnie sufitu — i wygląda na kompletny.
    Model, który przeczyta go w następnej turze, dostanie ucięte dane bez śladu obcięcia."""
    cel = tmp_path / "duzy.bin"

    wynik = exec_server.run_command(
        f"dd if=/dev/zero of={cel} bs=1M count={exec_server._MAX_FILE_MB + 5}", cwd=str(tmp_path)
    )

    assert "usuń go" in str(wynik["stderr"])
    assert cel.exists()


def test_the_cpu_backstop_never_gets_in_front_of_the_wall_clock_timeout():
    """Bezpiecznik czasu procesora ma łapać WYŁĄCZNIE proces, który wyszedł z grupy.

    Gdyby stanął poniżej maksymalnego czasu ściennego, ubijałby poprawne, długie polecenia
    pierwszoplanowe — i to sygnałem, którego wykonawca świadomie NIE tłumaczy na zdanie, bo
    z założenia nie powinien padać. Relacja tych dwóch liczb jest więc całą jego poprawnością
    i jedyną własnością, którą da się tu sprawdzić bez czekania dziesięciu minut.
    """
    assert exec_server._CPU_BACKSTOP_S > exec_server._MAX_TIMEOUT_S


def test_the_prologue_carries_the_cpu_backstop_with_a_matched_hard_ceiling():
    """Sufit miękki i twardy MUSZĄ być równe — bezpiecznik ma być nie do podniesienia przez
    proces, który właśnie próbuje go obejść. (Przy sufitach równych jądro wysyła `SIGKILL`
    zamiast `SIGXCPU`; tutaj to jest właściwe, bo żadnego komunikatu do tego sygnału nie ma.)"""
    prolog = exec_server._PROLOG_LIMITOW.format(fsize_kb=1, nofile=1, cpu_s=42)

    assert "ulimit -S -t 42" in prolog
    assert "ulimit -H -t 42" in prolog
