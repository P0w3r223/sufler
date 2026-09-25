"""Gniazdo kontrolne menedżera (ADR infra 0012) — mapowanie czasowników na odpowiedzi.

Protokół jest bliźniaczy do wykonawcy: żądanie → czasownik → odpowiedź, a KAŻDY błąd wraca jako
``{"error": ...}``, nie zerwanie (cisza wyglądałaby dla aplikacji jak zawieszenie). Tu sprawdzamy
sam dyspozytor na atrapie serwisu; transport gniazda ma pokrycie w teście klienta.

POSIX-only: moduł menedżera podnosi ``ImportError`` na Windows (gniazda unix, ``docker.sock``),
więc jego import wywróciłby ZBIERANIE całego pakietu testów. Bramka stoi PRZED importem i pyta
o ``fcntl`` — moduł, którego na Windows fizycznie nie ma — bo tylko brak modułu daje czysty skip
(od pytest 9.1 ``importorskip`` domyślnie łapie ``ModuleNotFoundError``, nie każdy ``ImportError``).
Ten sam strażnik i z tego samego powodu stoi w ``tests/adapters/test_exec_manager_client.py``
i ``test_exec_runner.py``; na Linuksie (obraz floty, CI) nie zmienia niczego.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fcntl", reason="menedżer wykonawców jest POSIX-only (gniazda unix)")

from sufler.adapters.inbound.exec_manager_server import (  # noqa: E402
    _dispatch,
    _install_stop_flag,
    serve,
)
from sufler.core.errors import ExecManagerError  # noqa: E402

_SCOPE = f"teams-graph/{'a' * 32}"


class _FakeService:
    def __init__(self, *, ensure_result: str = "/sock/x/exec.sock", boom: bool = False) -> None:
        self._ensure_result = ensure_result
        self._boom = boom
        self.reaped: list[str] = []
        self.shut_down = False

    def ensure(self, scope: str) -> str:
        if self._boom:
            raise ExecManagerError("scope odrzucony")
        return self._ensure_result

    def reap(self, scope: str) -> None:
        self.reaped.append(scope)

    def shutdown(self) -> None:
        self.shut_down = True


def _line(**payload) -> bytes:
    import json

    return json.dumps(payload).encode("utf-8") + b"\n"


def test_ensure_zwraca_sciezke_gniazda():
    response = _dispatch(_line(verb="ensure", scope=_SCOPE), _FakeService())  # type: ignore[arg-type]

    assert response == {"socket_path": "/sock/x/exec.sock"}


def test_reap_potwierdza_i_wola_serwis():
    service = _FakeService()

    response = _dispatch(_line(verb="reap", scope=_SCOPE), service)  # type: ignore[arg-type]

    assert response == {"ok": True}
    assert service.reaped == [_SCOPE]


def test_odmowa_serwisu_wraca_jako_error_nie_wyjatek():
    response = _dispatch(_line(verb="ensure", scope=_SCOPE), _FakeService(boom=True))  # type: ignore[arg-type]

    assert "error" in response
    assert "odrzucony" in response["error"]


def test_nieznany_czasownik_dostaje_error():
    response = _dispatch(_line(verb="zniszcz", scope=_SCOPE), _FakeService())  # type: ignore[arg-type]

    assert "error" in response
    assert "zniszcz" in response["error"]


def test_zepsute_zadanie_dostaje_error_nie_cisze():
    response = _dispatch(b"to nie jest json\n", _FakeService())  # type: ignore[arg-type]

    assert "error" in response


def test_brak_pola_scope_dostaje_error():
    response = _dispatch(_line(verb="ensure"), _FakeService())  # type: ignore[arg-type]

    assert "error" in response


# --- zatrzymanie procesu -----------------------------------------------------


def test_SIGTERM_ustawia_flage_zamiast_zabic_proces():
    """Bez handlera SIGTERM domyślna akcja kończy proces BEZ rozwijania ``finally`` w ``serve``.

    Kontener dostaje od Dockera właśnie SIGTERM, nie SIGINT, więc łapanie samego
    ``KeyboardInterrupt`` nie robiło nic: każde ``docker stop`` menedżera zostawiało wszystkie
    kontenery wykonawców żywe, po jednym na rozmowę. Ta sonda jest zarazem dowodem negatywnym —
    gdyby handler zniknął, SIGTERM zabiłby proces testów, a nie tylko wywrócił asercję.
    """
    import os
    import signal

    poprzednie = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        stop = _install_stop_flag()

        os.kill(os.getpid(), signal.SIGTERM)

        assert stop.wait(timeout=5.0)
    finally:
        for sig, handler in poprzednie.items():
            signal.signal(sig, handler)


def test_serve_sprzata_wykonawcow_gdy_flaga_zatrzymania_padnie(tmp_path):
    """Domknięcie tej samej ścieżki od drugiej strony: flaga → wyjście z pętli → ``shutdown``.

    Sam handler nie wystarczy — wartość ma dopiero to, że ustawiona flaga NAPRAWDĘ prowadzi do
    ubicia wykonawców. Tu biegnie prawdziwe gniazdo i prawdziwa pętla ``serve``, bo właśnie jej
    ``finally`` jest przedmiotem sondy.
    """
    import threading

    service = _FakeService()
    stop = threading.Event()
    watek = threading.Thread(
        target=serve,
        args=(service, tmp_path / "control.sock"),
        kwargs={"reap_interval_s": 0.05, "stop": stop},
        daemon=True,
    )
    watek.start()
    stop.set()
    watek.join(timeout=5.0)

    assert not watek.is_alive()
    assert service.shut_down
