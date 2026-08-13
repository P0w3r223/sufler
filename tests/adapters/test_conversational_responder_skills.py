"""Lista procedur dociera z drzwi do nagłówka sesji (ADR 0005).

Sedno: to, co czytnik zebrał z `/mnt/skills`, ma trafić do nagłówka KAŻDEJ tury — bo nagłówek
jest składany per turę, a lista jest stała w obrębie procesu. Bez procedur nagłówek zostaje
dokładnie taki, jak przed wprowadzeniem katalogu.
"""

from __future__ import annotations

import asyncio

from workmate.adapters.inbound.responder import ConversationalResponder, InboundMessage
from workmate.adapters.outbound.sqlite_conversations import SqliteConversationStore
from workmate.core.application.conversations import ConversationService
from workmate.core.ports.llm import AgentResult, AssistantTurn, UserText


class _HeaderRecordingRuntime:
    """Atrapa runtime'u — notuje nagłówek sesji z każdej tury."""

    def __init__(self) -> None:
        self.headers: list[str] = []

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
        self.headers.append(session_header)
        return AgentResult(
            reply="odp", entries=(UserText(query), AssistantTurn("odp", ())), stop_reason="end_turn"
        )


def _run(**kwargs) -> _HeaderRecordingRuntime:
    runtime = _HeaderRecordingRuntime()
    responder = ConversationalResponder(
        runtime,
        ConversationService(SqliteConversationStore(":memory:"), max_context_tokens=1000),
        channel="teams_graph",
        **kwargs,
    )
    asyncio.run(responder.respond(InboundMessage(text="hej", conversation_id="t/c/r")))
    return runtime


def test_bez_procedur_naglowek_nie_wspomina_o_skillach():
    assert "Skills available" not in _run().headers[0]


def test_procedury_trafiaja_do_naglowka_sesji():
    runtime = _run(skills=(("brief-projektu", "Użyj, gdy ktoś prosi o jednostronicówkę."),))

    header = runtime.headers[0]
    assert "Skills available in /mnt/skills/" in header
    assert "- brief-projektu — Użyj, gdy ktoś prosi o jednostronicówkę." in header


def test_naglowek_mowi_o_przepisaniu_krokow_do_brudnopisu():
    """Fakt o świecie, nie zachęta: czyszczenie kontekstu (ADR 0058) zdejmuje najstarsze
    wyniki poleceń, a procedura wczytana `cat`-em na starcie długiego zadania jest pierwsza
    w kolejce. Bez tego zdania traci się ją dokładnie wtedy, gdy jest najbardziej potrzebna."""
    header = _run(skills=(("x", "opis"),)).headers[0]

    assert "scratchpad" in header
