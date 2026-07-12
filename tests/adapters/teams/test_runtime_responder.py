"""Testy szwu responderów Teams (ADR 0008): RuntimeResponder i stub SaveNoteResponder.

Bez SDK i bez sieci: ``RuntimeResponder`` dostaje atrapę runtime'u (kaczo-typowaną),
a ``SaveNoteResponder`` jest jawnym stubem (zapis z Teams czeka na decyzję bramkowania).
"""
from __future__ import annotations

import asyncio

import pytest

from workmate.adapters.inbound.responder import (
    InboundMessage,
    RuntimeResponder,
    SaveNoteResponder,
)


class _FakeRuntime:
    """Atrapa ``AgentRuntime``: zapamiętuje zapytanie, zwraca ustaloną odpowiedź."""

    def __init__(self) -> None:
        self.seen: str | None = None

    def run(self, query: str, *, attachments: object = ()) -> str:
        self.seen = query
        return f"odpowiedź na: {query}"


def test_runtime_responder_delegates_to_runtime():
    runtime = _FakeRuntime()

    reply = asyncio.run(RuntimeResponder(runtime).respond(InboundMessage(text="co z mpwik?")))

    assert reply == "odpowiedź na: co z mpwik?"
    assert runtime.seen == "co z mpwik?"


def test_save_note_responder_is_an_unwired_stub():
    responder = SaveNoteResponder(write_service=object())  # type: ignore[arg-type]

    with pytest.raises(NotImplementedError):
        asyncio.run(responder.respond(InboundMessage(text="notatka")))
