"""Testy wpięcia fabryki narzędzia push-u obrazu 1:1 w ConversationalResponder (ADR 0027, A′3).

Sedno: gdy drzwi ustawią ``user_push_tool_factory``, responder DOKŁADA jej narzędzia PER TURĘ, ale
TYLKO gdy wiadomość niesie ``sender_id`` (cel dostawy wiąże się z NADAWCY, nie od modelu). Klucz to
``sender_id`` (AAD id nadawcy), NIE ``external_id`` wątku. Bez fabryki lub z pustym ``sender_id``
brak dodatkowego narzędzia. Błąd budowy fabryki degraduje (log, pusta lista), nie wywala tury. Bez
LLM/sieci: atrapa runtime notuje ``extra_tools`` + prawdziwy ``ConversationService`` nad SQLite.
"""

from __future__ import annotations

import asyncio
import logging

from workmate.adapters.inbound.responder import ConversationalResponder, InboundMessage
from workmate.adapters.outbound.sqlite_conversations import SqliteConversationStore
from workmate.core.application.conversations import ConversationService
from workmate.core.application.tools import ToolSpec
from workmate.core.ports.llm import AgentResult, AssistantTurn, UserText


class _RecordingRuntime:
    """Atrapa runtime — notuje nazwy ``extra_tools`` z każdej tury; zwraca stały wynik."""

    def __init__(self) -> None:
        self.extra_tools_per_call: list[list[str]] = []

    def run_turn(
        self,
        query: str,
        *,
        attachments: object = (),
        history: object = (),
        extra_tools: object = (),
        session_header: str = "",
        audit: object = None,
    ) -> AgentResult:
        self.extra_tools_per_call.append([spec.name for spec in extra_tools])  # type: ignore[attr-defined]
        return AgentResult(
            reply="odp",
            entries=(UserText(query), AssistantTurn("odp", ())),
            stop_reason="end_turn",
        )


def _service() -> ConversationService:
    return ConversationService(SqliteConversationStore(":memory:"), max_context_tokens=1000)


def _spec(name: str) -> ToolSpec:
    return ToolSpec(name, "opis", lambda **_: {"ok": True})


def test_user_push_tool_added_when_sender_id_present():
    runtime = _RecordingRuntime()
    responder = ConversationalResponder(
        runtime,
        _service(),
        channel="teams_graph",
        user_push_tool_factory=lambda sender_id: [_spec("send_image_to_user")],
    )

    asyncio.run(
        responder.respond(InboundMessage(text="hej", conversation_id="t/c/r", sender_id="u-anna"))
    )

    assert runtime.extra_tools_per_call[0] == ["send_image_to_user"]


def test_factory_receives_sender_id_not_conversation_id():
    """Fabryka dostaje ZAUFANY ``sender_id`` (AAD id nadawcy) jako CEL — nie klucz wątku."""
    seen: list[str] = []
    runtime = _RecordingRuntime()

    def factory(sender_id: str) -> list[ToolSpec]:
        seen.append(sender_id)
        return []

    responder = ConversationalResponder(
        runtime, _service(), channel="teams_graph", user_push_tool_factory=factory
    )

    asyncio.run(
        responder.respond(
            InboundMessage(text="hej", conversation_id="team/chan/root", sender_id="u-anna")
        )
    )

    assert seen == ["u-anna"]  # sender_id, NIE conversation_id


def test_no_user_push_tool_when_sender_id_empty():
    """Drzwi bez pojęcia nadawcy (puste ``sender_id``) → narzędzie się NIE buduje (brak celu)."""
    seen: list[str] = []
    runtime = _RecordingRuntime()

    def factory(sender_id: str) -> list[ToolSpec]:
        seen.append(sender_id)
        return [_spec("send_image_to_user")]

    responder = ConversationalResponder(
        runtime, _service(), channel="teams_graph", user_push_tool_factory=factory
    )

    asyncio.run(responder.respond(InboundMessage(text="hej", conversation_id="t/c/r")))

    assert runtime.extra_tools_per_call[0] == []
    assert seen == []  # fabryki nawet nie wołamy bez sender_id


def test_no_factory_means_no_user_push_tool_backward_compatible():
    runtime = _RecordingRuntime()
    responder = ConversationalResponder(runtime, _service(), channel="telegram")

    asyncio.run(
        responder.respond(InboundMessage(text="hej", conversation_id="c", sender_id="u-anna"))
    )

    assert runtime.extra_tools_per_call[0] == []


def test_factory_error_degrades_without_killing_turn(caplog):
    """Błąd budowy fabryki (np. brak zakresu) NIE zabija tury odczytowej — log + pusta lista."""
    runtime = _RecordingRuntime()

    def boom(sender_id: str) -> list[ToolSpec]:
        raise RuntimeError("brak zakresu czatu")

    responder = ConversationalResponder(
        runtime, _service(), channel="teams_graph", user_push_tool_factory=boom
    )

    with caplog.at_level(logging.WARNING):
        reply = asyncio.run(
            responder.respond(
                InboundMessage(text="hej", conversation_id="t/c/r", sender_id="u-anna")
            )
        )

    assert reply == "odp"  # tura się kończy mimo awarii fabryki
    assert runtime.extra_tools_per_call[0] == []  # degradacja do braku narzędzia
    assert any("push" in rec.message.lower() for rec in caplog.records)  # awaria zalogowana


def test_user_push_and_thread_tools_coexist_in_extra_tools():
    """Oba ustawione: narzędzie wątkowe i push-u obrazu współistnieją w jednej turze."""
    runtime = _RecordingRuntime()
    responder = ConversationalResponder(
        runtime,
        _service(),
        channel="teams_graph",
        thread_tool_factory=lambda external_id: [_spec("reply_on_thread")],
        user_push_tool_factory=lambda sender_id: [_spec("send_image_to_user")],
    )

    asyncio.run(
        responder.respond(InboundMessage(text="hej", conversation_id="t/c/r", sender_id="u-anna"))
    )

    assert runtime.extra_tools_per_call[0] == ["reply_on_thread", "send_image_to_user"]
