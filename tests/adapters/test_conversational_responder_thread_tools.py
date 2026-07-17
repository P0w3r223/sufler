"""Testy wpięcia fabryki narzędzia wątkowego w ConversationalResponder (ADR 0024, Faza 3b).

Sedno: gdy drzwi ustawią ``thread_tool_factory``, responder DOKŁADA jej narzędzia do ``extra_tools``
przekazywanych do ``run_turn`` (obok narzędzi katalogu roboczego, jeśli są). Gdy fabryki nie ma
(inne drzwi) — brak dodatkowych narzędzi (wsteczna zgodność). Bez LLM/sieci: atrapa runtime notuje
``extra_tools`` + prawdziwy ``ConversationService`` nad SQLite w pamięci.
"""

from __future__ import annotations

import asyncio

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


def test_thread_tool_from_factory_is_appended_to_extra_tools():
    runtime = _RecordingRuntime()
    responder = ConversationalResponder(
        runtime,
        _service(),
        channel="teams_graph",
        thread_tool_factory=lambda external_id: [_spec("reply_on_thread")],
    )

    asyncio.run(responder.respond(InboundMessage(text="hej", conversation_id="t/c/r")))

    assert runtime.extra_tools_per_call[0] == ["reply_on_thread"]


def test_factory_receives_external_id_from_conversation():
    """Fabryka dostaje ZAUFANY ``external_id`` wątku (nie tekst modelu) — do odczytu celu."""
    seen: list[str] = []
    runtime = _RecordingRuntime()

    def factory(external_id: str) -> list[ToolSpec]:
        seen.append(external_id)
        return []

    responder = ConversationalResponder(
        runtime, _service(), channel="teams_graph", thread_tool_factory=factory
    )

    asyncio.run(responder.respond(InboundMessage(text="hej", conversation_id="team/chan/root")))

    assert seen == ["team/chan/root"]


def test_no_factory_means_no_extra_tools_backward_compatible():
    runtime = _RecordingRuntime()
    responder = ConversationalResponder(runtime, _service(), channel="telegram")

    asyncio.run(responder.respond(InboundMessage(text="hej", conversation_id="c")))

    assert runtime.extra_tools_per_call[0] == []


def test_empty_factory_result_yields_no_extra_tools():
    """Wątek bez powiązania z issue/PR → fabryka zwraca [] → brak narzędzia zapisu."""
    runtime = _RecordingRuntime()
    responder = ConversationalResponder(
        runtime,
        _service(),
        channel="teams_graph",
        thread_tool_factory=lambda external_id: [],
    )

    asyncio.run(responder.respond(InboundMessage(text="hej", conversation_id="t/c/r")))

    assert runtime.extra_tools_per_call[0] == []


def test_workspace_and_thread_tools_coexist_in_extra_tools():
    """Oba ustawione: narzędzia katalogu roboczego i wątkowe współistnieją w jednej turze."""
    runtime = _RecordingRuntime()
    responder = ConversationalResponder(
        runtime,
        _service(),
        channel="teams_graph",
        workspace_catalog_factory=lambda scope: [_spec("create_file")],
        thread_tool_factory=lambda external_id: [_spec("reply_on_thread")],
    )

    asyncio.run(responder.respond(InboundMessage(text="hej", conversation_id="t/c/r")))

    assert runtime.extra_tools_per_call[0] == ["create_file", "reply_on_thread"]
