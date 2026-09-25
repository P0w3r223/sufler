"""Klient gniazda kontrolnego menedżera wykonawców (ADR infra 0012) — port ``ExecManager``.

Żyje po stronie APLIKACJI. Zna jeden czasownik — ``ensure(scope)`` — i jedno gniazdo: kontrolne
gniazdo menedżera (OSOBNE od gniazd wykonawców). Aplikacja pyta „daj mi gniazdo wykonawcy tej
rozmowy", menedżer stawia go (albo oddaje ciepłego) i zwraca ścieżkę. Dopiero potem aplikacja łączy
się z tym gniazdem wykonawcy, żeby wysłać polecenie (``SocketCommandRunner``).

Protokół jest bliźniaczy do wykonawcy (``exec_server``): jedno połączenie = jedno żądanie, po jednej
linii JSON w każdą stronę. Awaria transportu (brak menedżera, cisza, zły JSON) i odmowa menedżera
wracają jako ``ExecManagerError`` — warstwa ``Bash`` degraduje ją do wyniku z niezerowym kodem, więc
tura agenta się nie wywraca.
"""

from __future__ import annotations

import json
import logging
import socket
import sys
from pathlib import Path
from typing import Any

from sufler.core.errors import ExecManagerError

if sys.platform == "win32":  # pragma: no cover — nieosiągalne w obrazie
    raise ImportError("Klient menedżera wykonawców działa wyłącznie na POSIX (gniazda unix).")

logger = logging.getLogger(__name__)

# Zapas na start kontenera-wykonawcy (obraz slim, `network_mode: none` — rzędu setek ms) plus
# oczekiwanie menedżera na gotowość gniazda. Klient czeka DŁUŻEJ niż okno gotowości menedżera, żeby
# to menedżer zgłosił „nie wystał" czytelnym błędem, a nie klient zerwaniem po drugiej stronie.
_ENSURE_TIMEOUT_S = 30.0
# Sufit linii odpowiedzi — jak w ``exec_client``: ``settimeout`` obowiązuje per ``recv``, więc
# strumień sączony powoli nie przerywa odczytu ani po czasie, ani po rozmiarze. Odpowiedź
# menedżera to jedna ścieżka gniazda, czyli setki bajtów.
_MAX_RESPONSE_BYTES = 64 * 1024


class SocketExecManagerClient:
    """``ExecManager`` nad gniazdem kontrolnym menedżera: ``ensure(scope) → socket_path``."""

    def __init__(self, control_socket: Path) -> None:
        self._control_socket = control_socket

    def ensure(self, scope: str) -> str:
        """Poproś menedżera o gniazdo wykonawcy scope'a; zwróć ścieżkę albo podnieś
        ``ExecManagerError``."""
        try:
            payload = self._roundtrip({"verb": "ensure", "scope": scope})
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ExecManagerError(
                f"menedżer wykonawców niedostępny ({self._control_socket}): {exc}"
            ) from exc
        if "error" in payload:
            raise ExecManagerError(str(payload["error"]))
        socket_path = payload.get("socket_path")
        if not socket_path:
            raise ExecManagerError("menedżer nie zwrócił ścieżki gniazda wykonawcy")
        return str(socket_path)

    def _roundtrip(self, request: dict[str, Any]) -> dict[str, Any]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_ENSURE_TIMEOUT_S)
            sock.connect(str(self._control_socket))
            with sock.makefile("rwb") as stream:
                stream.write(json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n")
                stream.flush()
                line = stream.readline(_MAX_RESPONSE_BYTES)
        if not line:
            raise ValueError("menedżer zamknął połączenie bez odpowiedzi")
        if not line.endswith(b"\n"):
            raise ValueError(
                f"odpowiedź menedżera przekracza sufit {_MAX_RESPONSE_BYTES} B albo urwała się "
                "bez końca linii"
            )
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            raise ValueError("odpowiedź menedżera nie jest obiektem JSON")
        return parsed
