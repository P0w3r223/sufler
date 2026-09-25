"""Blokada jednej instancji drzwi cyklicznych (ADR 0035) — advisory, na pliku obok stanu.

Idempotencja przebiegu tygodniowego opiera się na stanie „ta osoba już dostała w tym tygodniu",
zapisywanym po każdej osobie. Dwa procesy czytające ten sam stan obeszłyby ją i wysłały ludziom
DWIE wiadomości z tym samym arkuszem — a to dokładnie ten błąd, którego cała reszta modułu
unika (człowiek nie odróżni ich i zaimportuje dwa razy).

Blokada jest advisory na dedykowanym ``<state>.lock``; system zwalnia ją przy zakończeniu procesu,
także po awarii, więc po ubitym procesie nie zostaje zawieszona blokada.

PRZENIESIONE z ``Powiadomienia_teams/single_instance.py`` (osobny projekt uv — patrz ADR 0035
§ port-vs-share). Utrzymywać zgodnie z oryginałem.
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


def acquire_single_instance_lock(state_path: Path) -> IO[str]:
    """Załóż wyłączną blokadę na ``<state_path>.lock`` i zwróć uchwyt (trzymaj do końca procesu).

    Rzuca ``AlreadyRunningError``, gdy blokada jest zajęta. Uchwyt MUSI żyć przez cały czas
    działania — zamknięcie zwalnia blokadę, więc używaj go jako context managera.
    """
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Tryb "a+" (nie "w"): NIE obcina pliku przy otwarciu, więc przegrywająca druga instancja
    # nie skasuje PID-u pierwszej, zanim (nieudanie) spróbuje założyć blokadę.
    handle = open(lock_path, "a+", encoding="utf-8")  # noqa: SIM115
    try:
        handle.seek(0)  # blokuj zawsze bajt 0 — spójna pozycja niezależnie od rozmiaru pliku
        _lock(handle)
    except OSError as exc:
        handle.close()
        raise AlreadyRunningError(
            f"Inna instancja już działa (blokada: {lock_path}). Uruchom tylko jedną naraz."
        ) from exc
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    logger.info("Blokada jednej instancji założona: %s (PID %d)", lock_path, os.getpid())
    return handle


def _lock(handle: IO[str]) -> None:
    """Nieblokujące, wyłączne zajęcie blokady — Windows (``msvcrt``) albo POSIX (``fcntl``).

    Rozgałęzienie po ``sys.platform`` (nie ``os.name``), żeby mypy analizował wyłącznie gałąź
    bieżącej platformy — inaczej na Windows sprawdzałby nieistniejący ``fcntl``.
    """
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
