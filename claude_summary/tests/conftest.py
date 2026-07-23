"""Wspólne narzędzia testów: fabryka linii transkryptu udającej realny prompt człowieka."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest


@pytest.fixture
def make_user_line() -> Callable[..., dict[str, Any]]:
    """Zwróć fabrykę budującą poprawną linię ``type:"user"`` z możliwością nadpisania pól."""

    def _make(**overrides: Any) -> dict[str, Any]:
        line: dict[str, Any] = {
            "type": "user",
            "promptSource": "typed",
            "origin": {"kind": "human"},
            "isSidechain": False,
            "sessionId": "sess-1",
            "cwd": "C:\\Users\\Test\\repo",
            "timestamp": "2026-07-17T09:00:00.000Z",
            "message": {"role": "user", "content": "zrób X"},
        }
        line.update(overrides)
        return line

    return _make
