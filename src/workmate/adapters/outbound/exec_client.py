"""Klient kontenera-wykonawcy (ADR 0057) — implementacja portu ``CommandRunner``.

Żyje po stronie APLIKACJI: składa polecenie ułożone przez model w linię JSON i wysyła je
gniazdem unix do wykonawcy, który jako jedyny ma powłokę. Aplikacja zachowuje sieć i sekrety,
ale nie uruchamia u siebie kodu od modelu; wykonawca uruchamia kod, ale nie ma dokąd wyjść.

Awaria wykonawcy (brak gniazda, zerwane połączenie, cisza) wraca jako ``CommandResult``
z niezerowym kodem, nie jako wyjątek: dla modelu to zwykła porażka polecenia, którą poprawi
w kolejnej turze, a nie powód do wywrócenia całej tury.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from workmate.core.errors import ExecManagerError
from workmate.core.ports.command import CommandResult

if TYPE_CHECKING:
    from workmate.core.ports.exec_manager import ExecManager

# Klient rozmawia z wykonawcą gniazdem unix, więc dzieli jego ograniczenie do POSIX. Import
# jest LENIWY w wiringu (jak Claude API), więc na maszynie deweloperskiej nikt go nie dotknie.
if sys.platform == "win32":  # pragma: no cover — nieosiągalne w obrazie
    raise ImportError("Klient wykonawcy działa wyłącznie na POSIX (gniazda unix).")

logger = logging.getLogger(__name__)

_DEFAULT_SOCKET = Path(os.environ.get("WORKMATE_EXEC_SOCKET", "/var/run/workmate/exec.sock"))
# Zapas ponad sufit wykonawcy (300 s): klient ma czekać DŁUŻEJ niż serwer, żeby timeout
# polecenia wracał jako wynik z flagą ``timed_out``, a nie jako zerwanie po stronie klienta —
# odwrotna kolejność gubiłaby informację, co się właściwie stało.
_CLIENT_MARGIN_S = 30.0
# Sufit linii ODPOWIEDZI — lustro ``_MAX_REQUEST_BYTES`` wykonawcy. ``settimeout`` obowiązuje
# per ``recv``, więc strumień sączony po bajcie nie przerwie odczytu ani po czasie, ani po
# rozmiarze: bez sufitu jedna zepsuta (albo wroga) odpowiedź rośnie w pamięci bez granicy.
# Realna odpowiedź jest znacznie mniejsza — wykonawca przycina każdy strumień do 64 KiB.
_MAX_RESPONSE_BYTES = 1024 * 1024


class SocketCommandRunner:
    """``CommandRunner`` nad gniazdem unix kontenera-wykonawcy."""

    def __init__(self, socket_path: Path = _DEFAULT_SOCKET) -> None:
        self._socket_path = socket_path

    def run(self, command: str, *, cwd: str = "", timeout_s: float = 0) -> CommandResult:
        """Wyślij polecenie do wykonawcy i zwróć wynik; awarię transportu zamień na wynik."""
        request = json.dumps(
            {"command": command, "cwd": cwd, "timeout_s": timeout_s}, ensure_ascii=False
        )
        try:
            payload = self._roundtrip(request, timeout_s)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Wykonawca niedostępny (%s): %s", self._socket_path, exc)
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr=f"Wykonawca poleceń jest niedostępny: {exc}",
            )
        return CommandResult(
            exit_code=int(payload.get("exit_code", -1)),
            stdout=str(payload.get("stdout", "")),
            stderr=str(payload.get("stderr", "")),
            truncated=bool(payload.get("truncated", False)),
            timed_out=bool(payload.get("timed_out", False)),
        )

    def _roundtrip(self, request: str, timeout_s: float) -> dict[str, Any]:
        """Jedno połączenie = jedno polecenie; czytamy dokładnie jedną linię odpowiedzi."""
        deadline = (timeout_s or 60.0) + _CLIENT_MARGIN_S
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(deadline)
            sock.connect(str(self._socket_path))
            with sock.makefile("rwb") as stream:
                stream.write(request.encode("utf-8") + b"\n")
                stream.flush()
                line = stream.readline(_MAX_RESPONSE_BYTES)
        if not line:
            raise ValueError("wykonawca zamknął połączenie bez odpowiedzi")
        if not line.endswith(b"\n"):
            raise ValueError(
                f"odpowiedź wykonawcy przekracza sufit {_MAX_RESPONSE_BYTES} B albo urwała się "
                "bez końca linii"
            )
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            raise ValueError("odpowiedź wykonawcy nie jest obiektem JSON")
        return parsed


class ManagedCommandRunner:
    """``CommandRunner`` per rozmowa: najpierw ``ensure(scope)`` u menedżera, potem polecenie.

    Zastępuje stałe gniazdo (``SocketCommandRunner`` na jeden adres) modelem wykonawcy-per-rozmowa:
    dla scope'a rozmowy pyta menedżera o ścieżkę gniazda JEGO wykonawcy (który menedżer stawia
    on-demand i trzyma ciepłym), a dopiero potem wysyła tam polecenie. Dzięki temu każda rozmowa
    dostaje wykonawcę montujący WYŁĄCZNIE swój podkatalog brudnopisu — ścieżka bezwzględna nie
    sięga cudzej rozmowy, bo tamtej nie ma w drzewie (istota ADR 0012).

    ``ensure`` biegnie przy KAŻDYM poleceniu, nie raz na rozmowę: jest idempotentne (ciepły
    wykonawca zwraca uchwyt natychmiast), a płacenie nim za polecenie kupuje odporność na reap —
    jeśli wykonawca zniknął po TTL, następne polecenie po prostu postawi go z powrotem, zamiast
    trafić w martwe gniazdo.

    Niedostępność menedżera albo wykonawcy wraca jako ``CommandResult`` z kodem ``-1`` — jak każda
    inna awaria transportu w ADR 0057 — więc pętla agenta się nie wywraca, a model poprawia się
    w nowej turze.
    """

    def __init__(self, manager: ExecManager, scope: str) -> None:
        self._manager = manager
        self._scope = scope

    def run(self, command: str, *, cwd: str = "", timeout_s: float = 0) -> CommandResult:
        try:
            socket_path = self._manager.ensure(self._scope)
        except ExecManagerError as exc:
            logger.warning("Menedżer nie zapewnił wykonawcy scope %s: %s", self._scope, exc)
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr=f"Wykonawca poleceń jest niedostępny: {exc}",
            )
        return SocketCommandRunner(Path(socket_path)).run(command, cwd=cwd, timeout_s=timeout_s)
