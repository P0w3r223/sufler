"""Blokada jednej instancji drzwi cyklicznych — kopia w Suflerze nie miała ani jednego testu.

Moduł przeniesiono z ``Powiadomienia_teams`` (ADR 0035) i nakazuje sobie w docstringu „Utrzymywać
zgodnie z oryginałem". Zachowanie obu kopii jest zgodne, rozjechało się POKRYCIE: oryginał ma
własny plik testów, a kopia, która strzeże drzwi digestu (``teams_digest/app.py``) przed podwójną
wysyłką DM, nie miała żadnej referencji testowej. Wyłączenie samej blokady zostawiało cały pakiet
zielony (sprawdzone mutacją 2026-10-01).

``deploy/docker/Dockerfile`` twierdził przy tym, że przebieg w kontenerze „potwierdza gałąź POSIX
``fcntl``" — prawda dla obrazu siostrzanego, fałsz dla tego. Ten plik czyni tamto zdanie
prawdziwym: na Linuksie (w obrazie i w CI) testy idą przez ``fcntl``, na Windows przez ``msvcrt``.

``test_second_acquire_is_blocked``, ``test_lock_released_after_close`` i
``test_lock_file_created_next_to_state`` powtarzają testy z ``Powiadomienia_teams`` — wspólna
specyfikacja zachowania w obu pakietach zamiast wspólnego pakietu kodu; rozjazd zachowań zapala
test w tym repo, w którym powstał. Reszta dokłada własności, które źródło samo komentuje.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import IO

import pytest

from sufler.adapters.inbound.single_instance import (
    AlreadyRunningError,
    acquire_single_instance_lock,
)


def test_second_acquire_is_blocked(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    first = acquire_single_instance_lock(state)
    try:
        with pytest.raises(AlreadyRunningError):
            acquire_single_instance_lock(state)  # druga instancja odmawia startu
    finally:
        first.close()


def test_second_acquire_fails_immediately_instead_of_waiting(tmp_path: Path) -> None:
    """Druga instancja ma DOSTAĆ błąd od razu, a nie czekać na zwolnienie blokady.

    Blokada czekająca (``LK_LOCK`` zamiast ``LK_NBLCK``, ``flock`` bez ``LOCK_NB``) przechodziła
    poprzedni test na Windows po ~9 s ponawiania, a na Linuksie wieszała go bez końca — pakiet nie
    ma timeoutu. Wątek z ``join(timeout=…)`` zamienia oba przypadki w czytelną porażkę.
    """
    state = tmp_path / "state.json"
    first = acquire_single_instance_lock(state)
    wynik: list[IO[str] | AlreadyRunningError] = []

    def druga_instancja() -> None:
        try:
            wynik.append(acquire_single_instance_lock(state))
        except AlreadyRunningError as exc:
            wynik.append(exc)

    watek = threading.Thread(target=druga_instancja, daemon=True)
    try:
        watek.start()
        watek.join(timeout=2.0)
        assert not watek.is_alive(), "druga instancja CZEKA na blokadę zamiast odmówić startu"
        assert len(wynik) == 1
        assert isinstance(wynik[0], AlreadyRunningError)
    finally:
        first.close()
        watek.join(timeout=15.0)  # blokada czekająca dostanie ją po zwolnieniu — posprzątaj
        for uchwyt in wynik:
            if not isinstance(uchwyt, AlreadyRunningError):
                uchwyt.close()


def test_lock_released_after_close(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    acquire_single_instance_lock(state).close()  # zajmij i zwolnij
    acquire_single_instance_lock(state).close()  # ponowne zajęcie po zwolnieniu OK


def test_lock_file_created_next_to_state(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    handle = acquire_single_instance_lock(state)
    try:
        assert (tmp_path / "state.json.lock").exists()
    finally:
        handle.close()


def test_lock_directory_is_created_when_missing(tmp_path: Path) -> None:
    """Katalog stanu powstaje po drodze — drzwi digestu startują na świeżym wolumenie."""
    state = tmp_path / "nowy" / "podkatalog" / "state.json"

    handle = acquire_single_instance_lock(state)

    try:
        assert state.parent.is_dir()
    finally:
        handle.close()


def _read_through(handle: IO[str]) -> str:
    """Odczytaj plik blokady PRZEZ uchwyt właściciela, nie z zewnątrz.

    Na Windows ``msvcrt.locking`` jest blokadą OBOWIĄZKOWĄ na bajcie 0, więc otwarcie tego pliku
    drugi raz w celu odczytu kończy się ``PermissionError``; na POSIX ``fcntl.flock`` jest advisory
    i odczyt by przeszedł. Asercja idąca przez uchwyt zachowuje się tak samo na obu platformach —
    inaczej test opisywałby system operacyjny, a nie moduł.
    """
    handle.seek(0)
    return handle.read().strip()


def test_lock_file_carries_the_owning_pid(tmp_path: Path) -> None:
    """PID w pliku to jedyny ślad „kto trzyma" przy diagnozie na serwerze."""
    state = tmp_path / "state.json"
    handle = acquire_single_instance_lock(state)

    try:
        assert _read_through(handle) == str(os.getpid())
    finally:
        handle.close()


def test_failed_acquire_does_not_truncate_the_incumbent_pid(tmp_path: Path) -> None:
    """Tryb ``a+`` (nie ``w``) jest komentowany w źródle jako istotny — tu jest sprawdzony.

    Przegrywająca druga instancja otwiera TEN SAM plik blokady, zanim spróbuje ją założyć.
    Z trybem ``w`` otwarcie obcięłoby plik i skasowało PID pierwszej — czyli dokładnie ten ślad,
    po którym operator ustala, który proces trzyma blokadę.
    """
    state = tmp_path / "state.json"
    first = acquire_single_instance_lock(state)
    try:
        with pytest.raises(AlreadyRunningError):
            acquire_single_instance_lock(state)

        assert _read_through(first) == str(os.getpid())
    finally:
        first.close()


def test_stale_pid_from_crashed_run_is_replaced(tmp_path: Path) -> None:
    """Plik blokady przeżywa ubity proces; nowy właściciel ma ZASTĄPIĆ stary PID, nie dopisać.

    Tryb ``a+`` dopisuje na końcu, więc bez ``truncate()`` plik po awarii niósłby
    ``<stary_pid><nowy_pid>`` — ślad, po którym operator nie ustali, kto trzyma blokadę.
    """
    state = tmp_path / "state.json"
    (tmp_path / "state.json.lock").write_text("99999999", encoding="utf-8")

    handle = acquire_single_instance_lock(state)

    try:
        assert _read_through(handle) == str(os.getpid())
    finally:
        handle.close()


@pytest.mark.skipif(sys.platform == "win32", reason="gałąź POSIX — wykonywana w obrazie i w CI")
def test_posix_branch_uses_fcntl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Jawnie nazwana gałąź, na którą powołuje się Dockerfile.

    Nie da się jej wykonać na maszynie deweloperskiej z Windows — i właśnie dlatego przebieg
    w kontenerze był jedynym miejscem, które ją pokrywa. Szpieg na ``fcntl.flock`` dowodzi, że
    ta ścieżka naprawdę biegnie, i to z flagami blokady wyłącznej, NIEBLOKUJĄCEJ.
    """
    import fcntl

    wywolania: list[int] = []
    oryginal = fcntl.flock

    def szpieg(fd: int, operation: int) -> None:
        wywolania.append(operation)
        oryginal(fd, operation)

    monkeypatch.setattr(fcntl, "flock", szpieg)

    state = tmp_path / "state.json"
    first = acquire_single_instance_lock(state)
    try:
        with pytest.raises(AlreadyRunningError):
            acquire_single_instance_lock(state)
    finally:
        first.close()

    # Bez `LOCK_NB` druga instancja nie dostałaby błędu, tylko zawisła na blokadzie.
    assert wywolania == [fcntl.LOCK_EX | fcntl.LOCK_NB] * 2
