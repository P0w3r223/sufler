"""``ScopeWorkspace`` nad systemem plików (ADR infra 0012) — podkatalogi scope'a i gniazdo.

Menedżer JEST właścicielem cyklu życia podkatalogu (ADR 0012 §4): tworzy go, zanim wystartuje
kontener-wykonawca, bo ``docker run`` z brakującym punktem bind-mountu (``VolumeOptions.Subpath``)
albo odmawia startu, albo — gorzej — zakłada go jako pusty katalog roota, do którego uid 10001 nie
zapisze. Dlatego ``prepare`` robi ``mkdir`` OBU podkatalogów (brudnopis + katalog gniazda) i nadaje
im właściciela 10001, zanim menedżer sięgnie do silnika.

Menedżer i aplikacja montują wolumen gniazd pod TĄ SAMĄ ścieżką (``sock_root``), więc ścieżka
gniazda policzona tutaj jest ważna po obu stronach — ``socket_path`` oddaje ją klientowi ``Bash``
bez tłumaczenia układów.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_SOCKET_NAME = "exec.sock"


class FilesystemScopeWorkspace:
    """Podkatalogi scope'a na wolumenach brudnopisu i gniazd; gotowość = pojawienie się gniazda.

    ``scratchpad_root`` i ``sock_root`` to punkty montażu wolumenów WIDZIANE PRZEZ MENEDŻERA — te
    same, które wykonawca dostanie jako ``Subpath`` (menedżer i wykonawca dzielą wolumeny, tylko
    wykonawca widzi wyłącznie swój podkatalog). ``sock_root`` jest zarazem ścieżką, pod którą
    APLIKACJA montuje wolumen gniazd, więc ``socket_path`` jest wspólny dla obu stron.
    """

    def __init__(
        self, scratchpad_root: Path, sock_root: Path, *, uid: int = 10001, gid: int = 10001
    ) -> None:
        self._scratchpad_root = scratchpad_root
        self._sock_root = sock_root
        self._uid = uid
        self._gid = gid

    def prepare(self, scope: str) -> None:
        """Utwórz idempotentnie podkatalogi brudnopisu i gniazda scope'a z uid wykonawcy."""
        for root in (self._scratchpad_root, self._sock_root):
            target = root / scope
            target.mkdir(parents=True, exist_ok=True)
            self._chown_tree(root, scope)

    def wait_ready(self, scope: str, timeout_s: float) -> bool:
        """Poll na pojawienie się pliku gniazda; ``True`` gdy jest, ``False`` po przekroczeniu okna.

        Sam plik gniazda wystarcza jako sygnał gotowości: wykonawca ``bind``uje je jako OSTATNI krok
        startu (``exec_server.serve``), więc jego obecność znaczy, że serwer nasłuchuje.
        """
        socket_file = self._sock_root / scope / _SOCKET_NAME
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if socket_file.exists():
                return True
            time.sleep(0.05)
        return socket_file.exists()

    def socket_path(self, scope: str) -> str:
        """Ścieżka gniazda w układzie montaży APLIKACJI (== układ menedżera, wspólny
        ``sock_root``)."""
        return str(self._sock_root / scope / _SOCKET_NAME)

    def cleanup(self, scope: str) -> None:
        """Usuń katalog gniazda scope'a (z resztką gniazda). Brudnopis ZOSTAJE — sprząta go
        prune_stale.

        Zostawiamy brudnopis rozmyślnie: gdy rozmowa wróci po reap, jej pliki nadal są, a ``ensure``
        po prostu postawi nowego wykonawcę na tym samym podkatalogu. Gniazdo jest ulotne — resztka
        po
        martwym wykonawcy tylko myliłaby ``wait_ready`` następnego.
        """
        sock_dir = self._sock_root / scope
        socket_file = sock_dir / _SOCKET_NAME
        socket_file.unlink(missing_ok=True)
        # Katalog kanału (rodzic hasha) zostaje — dzielą go inne rozmowy tego kanału. Niepusty albo
        # już usunięty katalog scope'a jest w porządku: sprzątanie gniazda jest best-effort.
        with contextlib.suppress(OSError):
            sock_dir.rmdir()

    def _chown_tree(self, root: Path, scope: str) -> None:
        """Nadaj właściciela 10001 podkatalogom scope'a (best-effort).

        ``chown`` na numeryczny uid wymaga roota — menedżer go ma (trzyma ``docker.sock``). Poza
        kontenerem (testy jako zwykły użytkownik, Windows bez ``os.chown``) po cichu odpuszczamy:
        istotny jest ``mkdir``, a właściciela i tak nadaje kontener produkcyjny.
        """
        if not hasattr(os, "chown"):  # pragma: no cover — Windows dev
            return
        # Kanał (rodzic) + hash (właściwy podkatalog); rodzic mógł już istnieć z innej rozmowy.
        channel, _, _ = scope.partition("/")
        for rel in (channel, scope):
            try:
                os.chown(root / rel, self._uid, self._gid)
            except (OSError, LookupError):
                logger.debug("chown %s pominięty (brak uprawnień/uid)", root / rel)
