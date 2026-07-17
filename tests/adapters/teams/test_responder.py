"""Testy szwu drzwi Teams (responder) — bez SDK, bez Azure."""

from __future__ import annotations

import asyncio

from workmate.adapters.inbound.responder import EchoResponder, InboundMessage


def test_echo_responder_confirms_receipt():
    reply = asyncio.run(EchoResponder().respond(InboundMessage(text="notatka ze spotkania")))

    assert reply == "Odebrałem notatkę: notatka ze spotkania"


def test_echo_responder_handles_empty_text():
    reply = asyncio.run(EchoResponder().respond(InboundMessage(text="")))

    assert reply == "Odebrałem notatkę: "


def test_inbound_message_carries_attribution():
    msg = InboundMessage(text="x", sender="Anna", conversation_id="conv-1")

    assert (msg.sender, msg.conversation_id) == ("Anna", "conv-1")
