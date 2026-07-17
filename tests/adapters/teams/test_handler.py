"""Test handlera Teams przez atrapę TurnContext/Activity — bez SDK, bez Azure.

Handler (``make_on_message``) jest kaczo-typowany, więc testujemy go strukturalnymi
atrapami (jak atrapy repo w ``conftest.py``), bez zainstalowanego extra ``teams``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from workmate.adapters.inbound.responder import EchoResponder, InboundMessage
from workmate.adapters.inbound.teams.bot import make_on_message


@dataclass
class _FakeActivity:
    text: str = ""
    from_property: object = None
    conversation: object = None


class _FakeContext:
    """Strukturalna atrapa TurnContext: zapamiętuje wysłane odpowiedzi."""

    def __init__(self, activity: _FakeActivity) -> None:
        self.activity = activity
        self.sent: list[str] = []

    async def send_activity(self, text: str) -> None:
        self.sent.append(text)


def test_handler_echoes_received_note():
    ctx = _FakeContext(_FakeActivity(text="ustalenia z mpwik"))

    asyncio.run(make_on_message(EchoResponder())(ctx))

    assert ctx.sent == ["Odebrałem notatkę: ustalenia z mpwik"]


def test_handler_uses_injected_responder():
    """Szew działa: handler zwraca dokładnie to, co wstrzyknięty responder."""

    class _Fixed:
        async def respond(self, message: InboundMessage) -> str:
            return f"[{message.text}]"

    ctx = _FakeContext(_FakeActivity(text="hej"))

    asyncio.run(make_on_message(_Fixed())(ctx))

    assert ctx.sent == ["[hej]"]


def test_handler_treats_none_text_as_empty():
    """Aktywności nie-tekstowe niosą text=None — nie odsyłamy 'None'."""
    ctx = _FakeContext(_FakeActivity(text=None))

    asyncio.run(make_on_message(EchoResponder())(ctx))

    assert ctx.sent == ["Odebrałem notatkę: "]


def test_handler_extracts_attribution_from_activity():
    @dataclass
    class _Sender:
        name: str = ""
        id: str = ""

    @dataclass
    class _Conv:
        id: str = ""

    captured: dict[str, str] = {}

    class _Capture:
        async def respond(self, message: InboundMessage) -> str:
            captured["sender"] = message.sender
            captured["conversation_id"] = message.conversation_id
            return "ok"

    ctx = _FakeContext(
        _FakeActivity(text="x", from_property=_Sender(name="Anna"), conversation=_Conv(id="c1"))
    )

    asyncio.run(make_on_message(_Capture())(ctx))

    assert captured == {"sender": "Anna", "conversation_id": "c1"}


@pytest.mark.parametrize(
    ("name", "ident", "expected"),
    [("Anna", "id-1", "Anna"), ("", "id-1", "id-1"), ("", "", "")],
)
def test_sender_attribution_falls_back_name_then_id(name, ident, expected):
    """_sender_of: name → id → "" (kolejność ważna dla przyszłego save_note)."""

    @dataclass
    class _Sender:
        name: str = ""
        id: str = ""

    captured: dict[str, str] = {}

    class _Capture:
        async def respond(self, message: InboundMessage) -> str:
            captured["sender"] = message.sender
            return "ok"

    ctx = _FakeContext(_FakeActivity(text="x", from_property=_Sender(name=name, id=ident)))

    asyncio.run(make_on_message(_Capture())(ctx))

    assert captured["sender"] == expected
