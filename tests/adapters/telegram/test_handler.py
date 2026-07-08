"""Test handlera Telegram przez atrapę Update/Message — bez SDK, bez tokenu.

Handler (``make_on_message``) jest kaczo-typowany, więc testujemy go strukturalnymi
atrapami (jak w drzwiach Teams), bez zainstalowanego extra ``telegram``.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from workmate.adapters.inbound.responder import EchoResponder, InboundMessage
from workmate.adapters.inbound.telegram.bot import make_on_message


class _FakeMessage:
    """Atrapa ``Message``: zapamiętuje wysłane odpowiedzi (``reply_text``)."""

    def __init__(self, text: str | None) -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


@dataclass
class _FakeUser:
    username: str = ""
    full_name: str = ""
    id: int = 0


@dataclass
class _FakeChat:
    id: int = 0


class _FakeUpdate:
    def __init__(self, message: object, user: object = None, chat: object = None) -> None:
        self.effective_message = message
        self.effective_user = user
        self.effective_chat = chat


def test_handler_echoes_received_message():
    msg = _FakeMessage("ustalenia z mpwik")

    asyncio.run(make_on_message(EchoResponder())(_FakeUpdate(msg)))

    assert msg.replies == ["Odebrałem notatkę: ustalenia z mpwik"]


def test_handler_uses_injected_responder():
    """Szew działa: handler zwraca dokładnie to, co wstrzyknięty responder."""

    class _Fixed:
        async def respond(self, message: InboundMessage) -> str:
            return f"[{message.text}]"

    msg = _FakeMessage("hej")

    asyncio.run(make_on_message(_Fixed())(_FakeUpdate(msg)))

    assert msg.replies == ["[hej]"]


def test_handler_treats_none_text_as_empty():
    msg = _FakeMessage(None)

    asyncio.run(make_on_message(EchoResponder())(_FakeUpdate(msg)))

    assert msg.replies == ["Odebrałem notatkę: "]


def test_handler_returns_silently_when_no_message():
    # Aktualizacja nie-wiadomościowa (effective_message=None) → brak wyjątku, brak odpowiedzi.
    asyncio.run(make_on_message(EchoResponder())(_FakeUpdate(None)))


def test_handler_extracts_attribution_from_update():
    captured: dict[str, str] = {}

    class _Capture:
        async def respond(self, message: InboundMessage) -> str:
            captured["sender"] = message.sender
            captured["conversation_id"] = message.conversation_id
            return "ok"

    update = _FakeUpdate(
        _FakeMessage("x"), user=_FakeUser(username="anna"), chat=_FakeChat(id=42)
    )

    asyncio.run(make_on_message(_Capture())(update))

    assert captured == {"sender": "anna", "conversation_id": "42"}
