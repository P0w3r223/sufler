"""Kontener-wykonawca (ADR 0057): serwer gniazda unix uruchamiający polecenia powłoki.

Osobny proces w osobnym kontenerze, wpięty tym samym obrazem co reszta floty (ADR 0044 —
jeden obraz, wiele entrypointów). Wykonawca ma montaże workspace'u i NIE MA sieci
(``network_mode: none``), więc kod pisany przez model nie ma dokąd wynieść danych.

Protokół jest celowo trywialny: jedno połączenie = jedno polecenie, żądanie i odpowiedź to
po jednej linii JSON. Bez sesji, bez stanu, bez strumieniowania — nie ma czego zsynchronizować
między procesami, a zerwane połączenie nie zostawia śladu poza zabitym procesem potomnym.

Granica zaufania biegnie na gnieździe: po jednej stronie jest aplikacja (wysyła polecenia
ułożone przez model), po drugiej powłoka. Wykonawca NIE ocenia treści polecenia — ocenianie
napisu powłoki jest zawodne, a bezpieczeństwo bierze się tu z tego, czego w kontenerze NIE MA
(sieci, zapisywalnych notatek, sekretów), nie z filtrowania.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

# Wykonawca jest POSIX-only: gniazda unix i grupy procesów nie istnieją na Windows, a jedynym
# miejscem jego uruchomienia jest kontener. Rozgałęzienie po ``sys.platform`` (nie ``os.name``)
# jak w ``single_instance.py`` — mypy analizuje wtedy wyłącznie gałąź bieżącej platformy,
# zamiast zgłaszać brak ``AF_UNIX``/``SIGKILL`` w stubach Windows.
if sys.platform == "win32":  # pragma: no cover — nieosiągalne w obrazie
    raise ImportError("workmate-exec działa wyłącznie na POSIX (gniazda unix, grupy procesów).")

logger = logging.getLogger(__name__)

# Sufit wyjścia ODDAWANEGO modelowi. Wyjście narzędzia wraca do kontekstu i jest odsyłane
# w każdej kolejnej turze (bezstratny replay), więc `cat` wielkiego pliku kosztowałby do końca
# rozmowy. Przycięcie jest oznaczane flagą, żeby model wiedział, że patrzy na fragment.
_MAX_OUTPUT_BYTES = 64 * 1024
_DEFAULT_TIMEOUT_S = 60.0
_MAX_TIMEOUT_S = 300.0
# Sufit linii żądania — zabezpiecza przed wyczerpaniem pamięci przez zepsutego klienta.
_MAX_REQUEST_BYTES = 1024 * 1024
_SOCKET_PATH = Path(os.environ.get("WORKMATE_EXEC_SOCKET", "/var/run/workmate/exec.sock"))
_DEFAULT_CWD = Path(os.environ.get("WORKMATE_EXEC_CWD", "/home/scratchpad"))


def _truncate(raw: bytes) -> tuple[str, bool]:
    """Przytnij strumień do sufitu i zdekoduj tolerancyjnie.

    Dekodujemy z ``errors="replace"``: polecenie może wypisać dowolne bajty (plik binarny,
    zerwane UTF-8), a wywrócenie wykonawcy na ``UnicodeDecodeError`` zamieniłoby zły wynik
    jednego polecenia w awarię całej tury.
    """
    if len(raw) <= _MAX_OUTPUT_BYTES:
        return raw.decode("utf-8", errors="replace"), False
    return raw[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"), True


def _resolve_cwd(requested: str) -> str | None:
    """Katalog roboczy: żądany → domyślny → katalog procesu (``None`` = dziedziczenie).

    Domyślny bywa nieobecny (wykonawca uruchomiony bez montaży, etap `test` obrazu), a
    ``Popen`` z nieistniejącym ``cwd`` rzuca ``FileNotFoundError``, którego komunikat mówi
    o katalogu, a nie o poleceniu — model dostawał wtedy błąd bez związku z tym, co zrobił.
    Dziedziczenie katalogu procesu jest zawsze poprawne, więc kończymy łańcuch na nim.
    """
    for candidate in (Path(requested) if requested else None, _DEFAULT_CWD):
        if candidate is not None and candidate.is_dir():
            return str(candidate)
    return None


def run_command(command: str, *, cwd: str = "", timeout_s: float = 0) -> dict[str, object]:
    """Uruchom polecenie w powłoce i zwróć wynik w postaci słownika protokołu.

    Proces potomny dostaje WŁASNĄ grupę procesów (``start_new_session``), żeby przy timeoucie
    zabić także jego potomków — bez tego ``sleep 999 &`` przeżywa zabicie powłoki i wykonawca
    zbiera sieroty. Po ``TimeoutExpired`` wysyłamy sygnał do całej grupy i dopiero wtedy
    czytamy to, co proces zdążył wypisać.
    """
    workdir = _resolve_cwd(cwd)
    limit = min(timeout_s or _DEFAULT_TIMEOUT_S, _MAX_TIMEOUT_S)

    proc = subprocess.Popen(  # noqa: S602 — powłoka to CEL tego narzędzia, nie przeoczenie
        ["/bin/bash", "-c", command],
        cwd=workdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    timed_out = False
    try:
        out, err = proc.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()

    stdout, cut_out = _truncate(out or b"")
    stderr, cut_err = _truncate(err or b"")
    return {
        "exit_code": proc.returncode if proc.returncode is not None else -1,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": cut_out or cut_err,
        "timed_out": timed_out,
    }


def _handle(conn: socket.socket) -> None:
    """Obsłuż jedno połączenie: linia JSON → wykonanie → linia JSON.

    Każdy błąd zamieniamy na ODPOWIEDŹ z niezerowym kodem, zamiast pozwolić mu zerwać
    połączenie: klient po drugiej stronie czeka na linię, a cisza po zerwaniu wygląda dla
    niego jak zawieszenie, nie jak porażka polecenia.
    """
    try:
        with conn, conn.makefile("rwb") as stream:
            line = stream.readline(_MAX_REQUEST_BYTES)
            if not line:
                return
            try:
                request = json.loads(line)
                result = run_command(
                    str(request["command"]),
                    cwd=str(request.get("cwd", "")),
                    timeout_s=float(request.get("timeout_s", 0)),
                )
            except Exception as exc:  # noqa: BLE001 — granica procesu, wszystko wraca jako wynik
                logger.warning("Żądanie odrzucone: %s", exc)
                result = {
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"Wykonawca odrzucił żądanie: {exc}",
                    "truncated": False,
                    "timed_out": False,
                }
            stream.write(json.dumps(result, ensure_ascii=False).encode("utf-8") + b"\n")
            stream.flush()
    except OSError:
        logger.warning("Połączenie zerwane w trakcie obsługi — pomijam")


def serve(socket_path: Path = _SOCKET_PATH) -> None:
    """Nasłuchuj na gnieździe unix, obsługując każde połączenie w osobnym wątku.

    Wątek na połączenie, nie pula: równoległość jest ograniczona liczbą tur agenta w locie
    (rzędu jednostek), a wątek czeka głównie na proces potomny. Gniazdo tworzymy od nowa —
    pozostałość po nieczystym zamknięciu blokowałaby ``bind``.
    """
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    # Uprawnienia gniazda są JEDYNĄ kontrolą dostępu do wykonawcy: kto może pisać do pliku
    # gniazda, ten może uruchomić polecenie. Wolumen dzielimy tylko z aplikacją.
    socket_path.chmod(0o660)
    server.listen(16)
    logger.info("Wykonawca nasłuchuje na %s", socket_path)
    try:
        while True:
            conn, _ = server.accept()
            threading.Thread(target=_handle, args=(conn,), daemon=True).start()
    finally:
        server.close()
        socket_path.unlink(missing_ok=True)


def main() -> None:
    """Entrypoint kontenera-wykonawcy: ``workmate-exec``."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    serve()
