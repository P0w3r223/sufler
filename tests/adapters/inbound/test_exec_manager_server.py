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

from workmate.adapters.inbound.exec_manager_server import _dispatch  # noqa: E402
from workmate.core.errors import ExecManagerError  # noqa: E402

_SCOPE = f"teams-graph/{'a' * 32}"


class _FakeService:
    def __init__(self, *, ensure_result: str = "/sock/x/exec.sock", boom: bool = False) -> None:
        self._ensure_result = ensure_result
        self._boom = boom
        self.reaped: list[str] = []

    def ensure(self, scope: str) -> str:
        if self._boom:
            raise ExecManagerError("scope odrzucony")
        return self._ensure_result

    def reap(self, scope: str) -> None:
        self.reaped.append(scope)


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
