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


class FakeOutbox:
    """Atrapa dwufazowej dostawy — notuje, czy migawka padła PRZED turą."""

    def __init__(self, notice="", on_deliver=None) -> None:
        self.snapshots: list[WorkspaceScope] = []
        self.delivered: list[WorkspaceScope] = []
        self._notice = notice
        self._on_deliver = on_deliver

    def snapshot(self, scope: WorkspaceScope) -> None:
        self.snapshots.append(scope)

    def deliver(self, scope: WorkspaceScope) -> str:
        self.delivered.append(scope)
        if self._on_deliver is not None:
            self._on_deliver(scope)
        return self._notice


def _respond(**kwargs) -> str:
    responder = ConversationalResponder(_Runtime(), _service(), channel="teams_graph", **kwargs)
    return asyncio.run(responder.respond(InboundMessage(text="hej", conversation_id="t/c/r")))


def test_bez_dostawy_odpowiedz_jest_niezmieniona():
    assert _respond() == "odp"


def test_komunikat_dostawy_dokleja_sie_do_odpowiedzi():
    reply = _respond(outbox_delivery=FakeOutbox("W załączniku: raport.md."))

    assert reply == "odp\n\nW załączniku: raport.md."


def test_pusty_komunikat_nie_zostawia_ogona():
    """Większość tur nic nie dostarcza — odpowiedź nie może wtedy dostać pustych linii."""
    assert _respond(outbox_delivery=FakeOutbox()) == "odp"


def test_dostawa_dostaje_scope_TEJ_rozmowy():
    """Scope z zaufanego (kanał, external_id) — model nie ma jak wskazać cudzej skrzynki."""
    outbox = FakeOutbox()

    _respond(outbox_delivery=outbox)

    assert outbox.delivered == [WorkspaceScope("teams_graph", "t/c/r")]
    assert outbox.snapshots == [WorkspaceScope("teams_graph", "t/c/r")], (
        "migawka musi paść PRZED turą — po niej nie da się już odróżnić pliku tej rozmowy "
        "od podłożonego wcześniej przez inną"
    )


def test_awaria_dostawy_NIE_zabiera_odpowiedzi_tekstowej():
    def wybuch(scope: WorkspaceScope) -> None:
        raise RuntimeError("Graph padł")

    assert _respond(outbox_delivery=FakeOutbox(on_deliver=wybuch)) == "odp"
