"""Blokada jednej instancji: uniemożliwia dwóm procesom równoczesny zapis do tego samego stanu.

Idempotencja zapisu do Shifts opiera się na sekwencji „commit stanu PRZED zapisem" W OBRĘBIE
jednego procesu — dwa procesy czytające ten sam pending obeszłyby ją i podwójnie zapisały do Shifts.
Blokada siedzi na dedykowanym pliku ``<state>.lock``; system zwalnia ją przy zakończeniu procesu
(także po awarii), więc nie zostają zawieszone blokady po martwym procesie.

Siła blokady RÓŻNI SIĘ między platformami i to widać w testach: POSIX-owe ``flock`` jest doradcze
(chroni przed instancjami tego programu, nie przed dowolnym zapisem z zewnątrz), a windowsowe
``msvcrt.locking`` jest obowiązkowe — właściciel nie wpuszcza do zablokowanego bajtu nawet
czytającego z tego samego procesu. Wspólny mianownik, na którym wolno polegać, to ten pierwszy.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)


class AlreadyRunningError(RuntimeError):
    """Inna instancja procesu już trzyma blokadę stanu — nie wolno uruchamiać drugiej."""


def _lock(handle: IO[str]) -> None:
    """Nieblokujące, wyłączne zajęcie blokady pliku — Windows (msvcrt) albo POSIX (fcntl).

    Rozgałęzienie po ``sys.platform`` (nie ``os.name``), żeby mypy analizował tylko gałąź bieżącej
    platformy — na Windows nie sprawdza POSIX-owego ``fcntl`` i odwrotnie.
    """
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def acquire_single_instance_lock(state_path: Path) -> IO[str]:
    """Załóż wyłączną blokadę na ``<state_path>.lock`` i zwróć uchwyt (trzymaj do końca procesu).

    Rzuca ``AlreadyRunningError``, gdy blokada jest już zajęta. Uchwyt musi żyć przez cały czas
    działania — zamknięcie zwalnia blokadę (użyj jako context managera). Blokada advisory: chroni
    przed innymi instancjami TEGO programu, nie przed dowolnym zapisem pliku z zewnątrz.
    """
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Tryb "a+" (nie "w"): NIE obcina pliku przy otwarciu, więc druga instancja nie kasuje PID
    # pierwszej, zanim (nieudanie) spróbuje założyć blokadę. Celowo BEZ context managera — uchwyt
    # musi żyć przez cały czas procesu (trzyma blokadę); wywołujący zamyka go na końcu.
    handle = open(lock_path, "a+", encoding="utf-8")  # noqa: SIM115
    try:
        handle.seek(0)  # blokuj zawsze bajt 0 (spójna pozycja niezależnie od rozmiaru pliku)
        _lock(handle)
    except OSError as exc:
        handle.close()
        raise AlreadyRunningError(
            f"Inna instancja już działa (blokada: {lock_path}). Uruchom tylko jedną naraz."
        ) from exc
    handle.seek(0)
    handle.truncate()  # właściciel blokady może pisać — zapisz świeży PID
    handle.write(str(os.getpid()))
    handle.flush()
    logger.info("Blokada jednej instancji założona: %s (PID %d)", lock_path, os.getpid())
    return handle
