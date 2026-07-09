"""Testy szwu ``ConversationalResponder`` (ADR 0010) — pamięć rozmowy w drzwiach.

Bez LLM i bez sieci: atrapa runtime (notuje przekazaną historię, zwraca kanned reply)
+ prawdziwy ``ConversationService`` nad SQLite w pamięci. Sprawdzamy, że kolejna tura
dostaje historię poprzednich, odpowiedź jest zapisywana, a rollover dokłada notkę.
"""
from __future__ import annotations

import asyncio

import pytest

from workmate.adapters.inbound.responder import ConversationalResponder, InboundMessage
from workmate.adapters.outbound.sqlite_conversations import SqliteConversationStore
from workmate.core.application.conversations import ConversationService


class _FakeRuntime:
    """Atrapa runtime — notuje ``(query, history)`` i zwraca stałą odpowiedź."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[tuple[str, list[object]]] = []

    def run(self, query: str, *, history: object = ()) -> str:
        self.calls.append((query, list(history)))  # type: ignore[arg-type]
        return self.reply


class _FailingRuntime:
    """Atrapa runtime, która rzuca — symuluje przejściowy błąd Claude API."""

    def run(self, query: str, *, history: object = ()) -> str:
        raise RuntimeError("runtime padł")


def test_second_turn_receives_prior_history_and_reply_is_recorded():
    store = SqliteConversationStore(":memory:")
    service = ConversationService(store, max_context_tokens=1000)
    runtime = _FakeRuntime("odpowiedz")
    responder = ConversationalResponder(runtime, service, channel="telegram")

    r1 = asyncio.run(
        responder.respond(InboundMessage(text="pierwsza", conversation_id="chat1"))
    )
    asyncio.run(
        responder.respond(InboundMessage(text="druga", conversation_id="chat1"))
    )

    assert r1 == "odpowiedz"
    # Druga tura: query "druga", a historia to poprzednia para (user + assistant).
    query2, history2 = runtime.calls[1]
    assert query2 == "druga"
    assert [type(h).__name__ for h in history2] == ["UserText", "AssistantTurn"]
    # W jednym wątku: 2 tury user + 2 assistant.
    active = store.active_conversation("telegram", "chat1")
    assert active is not None
    assert len(store.messages(active.id)) == 4


def test_rollover_prefixes_notice_on_context_limit():
    store = SqliteConversationStore(":memory:")
    service = ConversationService(store, max_context_tokens=3)
    runtime = _FakeRuntime("12345678")  # est 2 → z turą usera przekracza limit 3
    responder = ConversationalResponder(runtime, service, channel="telegram")

    asyncio.run(responder.respond(InboundMessage(text="12345678", conversation_id="c")))
    reply2 = asyncio.run(
        responder.respond(InboundMessage(text="1234", conversation_id="c"))
    )

    assert reply2.startswith("(Poprzednia rozmowa osiągnęła limit kontekstu")


def test_runtime_error_leaves_no_orphan_turn():
    store = SqliteConversationStore(":memory:")
    service = ConversationService(store, max_context_tokens=1000)
    responder = ConversationalResponder(_FailingRuntime(), service, channel="telegram")

    with pytest.raises(RuntimeError):
        asyncio.run(
            responder.respond(InboundMessage(text="czesc", conversation_id="chat1"))
        )

    # prepare_turn otworzyło rozmowę, ale błąd runtime → NIC nie utrwalono (brak sieroty).
    active = store.active_conversation("telegram", "chat1")
    assert active is not None
    assert store.messages(active.id) == []
    assert active.token_estimate == 0
