"""Test handlera drzwi Teams (delegowane) — szew ``ChannelMessage`` → ``Responder``.

Handler składa ``InboundMessage`` z ``ChannelMessage`` i deleguje do wstrzykniętego
respondera. Kluczowe: ``conversation_id`` (klucz PAMIĘCI wątku) podaje poller i musi trafić
do respondera bez zmian, a pusta odpowiedź mapuje się na ``None`` (poller nic nie wysyła).
Testujemy bez SDK — responder to atrapa strukturalna.
"""

from __future__ import annotations

import asyncio

from workmate.adapters.inbound.responder import EchoResponder, InboundMessage
from workmate.adapters.inbound.teams_graph.handler import make_handle_message
from workmate.adapters.inbound.teams_graph.selection import ChannelMessage


def _msg(**overrides: object) -> ChannelMessage:
    base: dict[str, object] = {
        "id": "m1",
        "thread_root_id": "root-1",
        "created": "2024-01-01T10:00:00Z",
        "sender_id": "u-anna",
        "sender_name": "Anna",
        "text": "ustalenia z mpwik",
    }
    base.update(overrides)
    return ChannelMessage(**base)  # type: ignore[arg-type]


def test_handler_echoes_message_text():
    handle = make_handle_message(EchoResponder())

    reply = asyncio.run(handle(_msg(text="ustalenia z mpwik"), "team/chan/root-1"))

    assert reply == "Odebrałem notatkę: ustalenia z mpwik"


def test_handler_passes_text_sender_and_conversation_id_to_responder():
    """Szew niesie atrybucję: text, sender_name→sender oraz klucz pamięci wątku."""
    captured: dict[str, str] = {}

    class _Capture:
        async def respond(self, message: InboundMessage) -> str:
            captured["text"] = message.text
            captured["sender"] = message.sender
            captured["conversation_id"] = message.conversation_id
            return "ok"

    handle = make_handle_message(_Capture())

    asyncio.run(
        handle(
            _msg(text="hej", sender_name="Anna"),
            "team-a/chan-1/root-42",
        )
    )

    assert captured == {
        "text": "hej",
        "sender": "Anna",
        "conversation_id": "team-a/chan-1/root-42",
    }


def test_handler_carries_sender_id_for_user_push_target():
    """Szew niesie ``sender_id`` (AAD id nadawcy) → CEL wyjściowej dostawy 1:1 (ADR 0027).

    Bez tego pola narzędzie push-u obrazu się nie zbuduje w responderze, więc handler MUSI je
    przenieść z ``ChannelMessage.sender_id`` do ``InboundMessage.sender_id`` bez zmian.
    """
    captured: dict[str, str] = {}

    class _Capture:
        async def respond(self, message: InboundMessage) -> str:
            captured["sender_id"] = message.sender_id
            return "ok"

    handle = make_handle_message(_Capture())

    asyncio.run(handle(_msg(sender_id="u-anna-aad"), "team/chan/root"))

    assert captured["sender_id"] == "u-anna-aad"


def test_handler_returns_injected_responder_reply():
    """Szew działa: handler zwraca dokładnie to, co wstrzyknięty responder."""

    class _Fixed:
        async def respond(self, message: InboundMessage) -> str:
            return f"[{message.text}]"

    handle = make_handle_message(_Fixed())

    assert asyncio.run(handle(_msg(text="x"), "c")) == "[x]"


def test_handler_maps_empty_reply_to_none():
    """Pusta odpowiedź respondera → None, żeby poller nic nie wysyłał."""

    class _Silent:
        async def respond(self, message: InboundMessage) -> str:
            return ""

    handle = make_handle_message(_Silent())

    assert asyncio.run(handle(_msg(), "c")) is None
