"""Menedżer wykonawców per rozmowa (ADR infra 0012) — proces ``workmate-exec-manager``.

Jedyny komponent floty trzymający ``docker.sock``. Wystawia APLIKACJI wąskie gniazdo kontrolne z
czasownikami menedżera (``ensure``/``reap``), sam zaś składa wykonawców ze STAŁEGO szablonu przez
``ContainerEngine`` (Docker po gnieździe) i pilnuje ich cyklu życia (``ExecManagerService``: reap po
TTL, limit N z LRU, reconcile po restarcie). Nie uruchamia kodu modelu i nie ma sieci do jego wejść
—
jego jedyne niezaufane wejście to ``scope`` z aplikacji, walidowany ściśle, zanim dotknie Dockera.

Protokół gniazda kontrolnego jest bliźniaczy do wykonawcy (``exec_server``): jedno połączenie =
jedno
żądanie, po jednej linii JSON w każdą stronę, bez sesji i stanu. Każdy błąd wraca jako ODPOWIEDŹ
``{"error": ...}``, nie zerwanie — cisza po drugiej stronie wyglądałaby dla aplikacji jak
zawieszenie.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
import sys
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if sys.platform == "win32":  # pragma: no cover — nieosiągalne w obrazie
    raise ImportError(
        "workmate-exec-manager działa wyłącznie na POSIX (gniazda unix, docker.sock)."
    )

from workmate.core.application.exec_manager import ExecManagerService
from workmate.core.errors import ExecManagerError

if TYPE_CHECKING:
    from workmate.config import ExecManagerSettings

logger = logging.getLogger(__name__)

# Sufit linii żądania — zabezpiecza przed wyczerpaniem pamięci przez zepsutego klienta (jak
# exec_server).
_MAX_REQUEST_BYTES = 64 * 1024
# Sufit oczekiwania w JEDNYM połączeniu. Gniazdo zaakceptowane NIE dziedziczy timeoutu nasłuchu,
# więc bez tego klient, który się łączy i milczy, wieszałby wątek i deskryptor bez końca. ``ensure``
# bywa wolne (start kontenera + gotowość), więc sufit jest hojny, ale skończony.
_CONN_TIMEOUT_S = 60.0


def _handle(conn: socket.socket, service: ExecManagerService) -> None:
    """Obsłuż jedno połączenie kontrolne: linia JSON → czasownik menedżera → linia JSON.

    Każdy błąd (zły JSON, nieznany czasownik, niepoprawny scope, odmowa silnika) zamieniamy na
    ODPOWIEDŹ z polem ``error``. ``ExecManagerError`` niesie już czytelny komunikat po polsku; inne
    wyjątki logujemy z tracebackiem, a klientowi oddajemy krótkie „menedżer odrzucił żądanie".

    Milczącego klienta odcina ``_CONN_TIMEOUT_S`` (``TimeoutError`` jest podklasą ``OSError``, więc
    łapiemy je razem z zerwaniem), żeby jeden zawieszony wątek nie zjadał deskryptora bez końca.
    """
    conn.settimeout(_CONN_TIMEOUT_S)
    try:
        with conn, conn.makefile("rwb") as stream:
            line = stream.readline(_MAX_REQUEST_BYTES)
            if not line:
                return
            response = _dispatch(line, service)
            stream.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
            stream.flush()
    except OSError:
        logger.warning("Połączenie kontrolne zerwane w trakcie obsługi — pomijam")


def _dispatch(line: bytes, service: ExecManagerService) -> dict[str, Any]:
    """Zmapuj żądanie na czasownik menedżera; zwróć słownik odpowiedzi (z ``error`` przy
    porażce)."""
    try:
        request = json.loads(line)
        verb = str(request["verb"])
        scope = str(request["scope"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        return {"error": f"menedżer odrzucił żądanie: {exc}"}
    try:
        if verb == "ensure":
            return {"socket_path": service.ensure(scope)}
        if verb == "reap":
            service.reap(scope)
            return {"ok": True}
        return {"error": f"nieznany czasownik menedżera: {verb!r}"}
    except ExecManagerError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — granica procesu: wszystko wraca jako odpowiedź
        logger.warning("Czasownik %s scope %s wywrócił się", verb, scope, exc_info=True)
        return {"error": f"menedżer nie zdołał obsłużyć {verb}: {exc}"}


def _reap_loop(service: ExecManagerService, interval_s: float, stop: threading.Event) -> None:
    """Cyklicznie reapuj bezczynnych wykonawców, aż ktoś ustawi ``stop`` (SIGTERM/Ctrl-C)."""
    while not stop.wait(interval_s):
        with contextlib.suppress(Exception):
            service.reap_idle()


def serve(
    service: ExecManagerService,
    control_socket: Path,
    *,
    reap_interval_s: float,
    socket_owner: tuple[int, int] | None = None,
    stop: threading.Event | None = None,
) -> None:
    """Nasłuchuj na gnieździe kontrolnym; obsłuż każde połączenie w osobnym wątku; reapuj w tle.

    ``stop`` pozwala testom/sygnałom domknąć pętlę; bez niego serwer żyje do przerwania procesu.
    Reconcile robimy PRZED nasłuchem (osobno, w ``main``), żeby menedżer wszedł na czysto po
    restarcie. ``socket_owner`` (uid, gid) nadaje gniazdo kontrolne aplikacji: menedżer biegnie jako
    root (trzyma ``docker.sock``), więc bez chown gniazdo miałoby właściciela root i aplikacja (uid
    wykonawcy) nie sięgnęłaby po nie przy 0660. Best-effort — poza rootem (testy) odpuszcza cicho.
    """
    stop = stop if stop is not None else threading.Event()
    control_socket.parent.mkdir(parents=True, exist_ok=True)
    control_socket.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(control_socket))
    # Uprawnienia gniazda są kontrolą dostępu do menedżera: wolumen gniazda kontrolnego dzielą
    # WYŁĄCZNIE aplikacja i menedżer, więc 0660 wystarcza (jak gniazdo wykonawcy w ADR 0057).
    control_socket.chmod(0o660)
    if socket_owner is not None and hasattr(os, "chown"):
        with contextlib.suppress(OSError):
            os.chown(control_socket, *socket_owner)
    server.listen(16)
    server.settimeout(1.0)  # budzik, żeby ``stop`` był sprawdzany mimo braku połączeń
    logger.info("Menedżer wykonawców nasłuchuje na %s", control_socket)

    reaper = threading.Thread(target=_reap_loop, args=(service, reap_interval_s, stop), daemon=True)
    reaper.start()
    try:
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            threading.Thread(target=_handle, args=(conn, service), daemon=True).start()
    finally:
        stop.set()
        server.close()
        control_socket.unlink(missing_ok=True)
        service.shutdown()


def build_service(settings: ExecManagerSettings) -> ExecManagerService:
    """Złóż menedżera z konfiguracji: szablon ``docker run`` → silnik → workspace → serwis cyklu
    życia."""
    from workmate.adapters.outbound.docker_engine import DockerHttpEngine, DockerRunTemplate
    from workmate.adapters.outbound.filesystem_scope_workspace import FilesystemScopeWorkspace

    template = DockerRunTemplate(
        image=settings.image,
        scratchpad_volume=settings.scratchpad_volume,
        sock_volume=settings.sock_volume,
        data_volume=settings.data_volume,
        notes_dir=settings.notes_dir,
        skills_source=settings.skills_source,
    )
    engine = DockerHttpEngine(template, socket_path=str(settings.docker_socket))
    workspace = FilesystemScopeWorkspace(
        settings.scratchpad_root,
        settings.sock_root,
        uid=settings.exec_uid,
        gid=settings.exec_gid,
    )
    return ExecManagerService(
        engine,
        workspace,
        run_id=uuid.uuid4().hex,
        max_executors=settings.max_executors,
        idle_ttl_s=float(settings.idle_ttl_s),
        ready_timeout_s=float(settings.ready_timeout_s),
    )


def main() -> None:
    """Entrypoint menedżera: waliduj konfigurację, reconcile po restarcie, serwuj gniazdo
    kontrolne."""
    from workmate.config import ExecManagerSettings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = ExecManagerSettings.from_env()
    settings.validate()
    service = build_service(settings)
    service.reconcile()
    try:
        serve(
            service,
            settings.control_socket,
            reap_interval_s=float(settings.reap_interval_s),
            socket_owner=(settings.exec_uid, settings.exec_gid),
        )
    except KeyboardInterrupt:  # pragma: no cover — sygnał zatrzymania kontenera
        logger.info("Menedżer wykonawców zatrzymany")
