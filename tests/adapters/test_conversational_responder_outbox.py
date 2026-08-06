"""Wpięcie dostawy ze skrzynki nadawczej w ``ConversationalResponder`` (ADR 0009 paczki).

Sedno: dostawa biegnie PO turze, jej komunikat dokleja się do odpowiedzi, a jej awaria nie
może zabrać rozmówcy odpowiedzi tekstowej — tura już się udała i została utrwalona. Bez LLM
i sieci: atrapa runtime'u + prawdziwy ``ConversationService`` nad SQLite w pamięci.
"""

from __future__ import annotations

import asyncio

from workmate.adapters.inbound.responder import ConversationalResponder, InboundMessage
from workmate.adapters.outbound.sqlite_conversations import SqliteConversationStore
from workmate.core.application.conversations import ConversationService
from workmate.core.domain.workspace import WorkspaceScope
from workmate.core.ports.llm import AgentResult, AssistantTurn, UserText


class _Runtime:
    """Atrapa runtime'u: zawsze ta sama odpowiedź tekstowa."""

    def run_turn(
        self,
        query: str,
        *,
        attachments: object = (),
        history: object = (),
        extra_tools: object = (),
        session_header: str = "",
    ) -> AgentResult:
        return AgentResult(
            reply="odp", entries=(UserText(query), AssistantTurn("odp", ())), stop_reason="end_turn"
        )


def _service() -> ConversationService:
    return ConversationService(SqliteConversationStore(":memory:"), max_context_tokens=1000)


def _respond(**kwargs) -> str:
    responder = ConversationalResponder(_Runtime(), _service(), channel="teams_graph", **kwargs)
    return asyncio.run(responder.respond(InboundMessage(text="hej", conversation_id="t/c/r")))


def test_bez_dostawy_odpowiedz_jest_niezmieniona():
    assert _respond() == "odp"


def test_komunikat_dostawy_dokleja_sie_do_odpowiedzi():
    reply = _respond(outbox_delivery=lambda scope: "W załączniku: raport.md.")

    assert reply == "odp\n\nW załączniku: raport.md."


def test_pusty_komunikat_nie_zostawia_ogona():
    """Większość tur nic nie dostarcza — odpowiedź nie może wtedy dostać pustych linii."""
    assert _respond(outbox_delivery=lambda scope: "") == "odp"


def test_dostawa_dostaje_scope_TEJ_rozmowy():
    """Scope z zaufanego (kanał, external_id) — model nie ma jak wskazać cudzej skrzynki."""
    seen: list[WorkspaceScope] = []

    _respond(outbox_delivery=lambda scope: seen.append(scope) or "")

    assert seen == [WorkspaceScope("teams_graph", "t/c/r")]


def test_awaria_dostawy_NIE_zabiera_odpowiedzi_tekstowej():
    def wybuch(scope: WorkspaceScope) -> str:
        raise RuntimeError("Graph padł")

    assert _respond(outbox_delivery=wybuch) == "odp"
