"""Testy runtime'u agenta (Faza 2, M1) — atrapa ``LLMClient``, bez sieci.

Runtime zależy tylko od portu LLM i katalogu narzędzi, więc scenariusze pętli
tool-use sprawdzamy atrapą zwracającą zaplanowane odpowiedzi (jak atrapy repo w
``conftest.py``).
"""
from __future__ import annotations

from workmate.core.agent.runtime import AgentRuntime
from workmate.core.application.tools import ToolSpec
from workmate.core.domain.pricing import TokenUsage
from workmate.core.ports.llm import (
    AssistantTurn,
    LLMResponse,
    ToolCall,
    ToolResults,
    UserText,
)


def _spec(name: str, fn) -> ToolSpec:
    return ToolSpec(name, name, fn)


class _ScriptedLLM:
    """Atrapa ``LLMClient``: oddaje kolejne zaplanowane odpowiedzi, zapamiętuje transkrypty."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.transcripts: list[list] = []
        self.tools_seen: list[list[str]] = []

    def complete(self, *, system, transcript, tools):
        self.transcripts.append(list(transcript))
        self.tools_seen.append([t.name for t in tools])
        return self._responses.pop(0)


def test_runtime_returns_text_when_model_stops():
    llm = _ScriptedLLM([LLMResponse(text="gotowe")])

    result = AgentRuntime(llm, []).run("pytanie")

    assert result == "gotowe"


def test_runtime_dispatches_tool_and_feeds_result_back():
    seen: list[str] = []

    def search(query: str) -> dict:
        seen.append(query)
        return {"count": 1, "results": ["x"]}

    llm = _ScriptedLLM(
        [
            LLMResponse(tool_calls=(ToolCall("t1", "search_notes", {"query": "mpwik"}),)),
            LLMResponse(text="Znalazłem 1 notatkę."),
        ]
    )

    result = AgentRuntime(llm, [_spec("search_notes", search)]).run("co z mpwik?")

    assert result == "Znalazłem 1 notatkę."
    assert seen == ["mpwik"]  # narzędzie dostało argumenty od modelu
    # druga tura widzi wynik narzędzia w transkrypcie
    second = llm.transcripts[1]
    tool_results = [e for e in second if isinstance(e, ToolResults)]
    assert tool_results and '"count": 1' in tool_results[0].outputs[0].content


def test_runtime_marks_unknown_tool_as_error():
    def real(query: str) -> dict:
        return {"ok": True}

    llm = _ScriptedLLM(
        [
            LLMResponse(tool_calls=(ToolCall("t1", "nieistnieje", {}),)),
            LLMResponse(text="ok"),
        ]
    )

    AgentRuntime(llm, [_spec("real", real)]).run("x")

    outputs = [e for e in llm.transcripts[1] if isinstance(e, ToolResults)][0].outputs
    assert outputs[0].is_error is True


def test_runtime_returns_recoverable_error_for_bad_tool_arguments():
    """Model podał złą nazwę argumentu → błąd narzędzia, pętla się NIE wywraca."""

    def only_query(query: str) -> dict:
        return {"ok": True}

    llm = _ScriptedLLM(
        [
            LLMResponse(tool_calls=(ToolCall("t1", "search", {"nieznany_arg": 1}),)),
            LLMResponse(text="poprawiłem"),
        ]
    )

    result = AgentRuntime(llm, [_spec("search", only_query)]).run("x")

    assert result == "poprawiłem"  # zapytanie się nie wywróciło
    outputs = [e for e in llm.transcripts[1] if isinstance(e, ToolResults)][0].outputs
    assert outputs[0].is_error is True
    assert "search" in outputs[0].content


def test_runtime_respects_iteration_budget():
    """Model zawsze chce narzędzia → pętla zatrzymuje się po budżecie iteracji."""

    def tool(**_kwargs) -> dict:
        return {"ok": True}

    class _AlwaysTool:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, *, system, transcript, tools):
            self.calls += 1
            return LLMResponse(text="myślę", tool_calls=(ToolCall("t", "tool", {}),))

    llm = _AlwaysTool()
    result = AgentRuntime(llm, [_spec("tool", tool)], max_tool_iterations=3).run("x")

    assert llm.calls == 3
    assert result == "myślę"  # ostatni tekst zwrócony po wyczerpaniu budżetu


# --- run_turn: wpisy DO ZAPISU + stop_reason (ADR 0011) ------------------------


def test_run_turn_truncated_turn_persists_nothing():
    """``max_tokens`` = tura niedomknięta: NIC nie zapisujemy (inwariant zapisu, ADR 0011).

    Pamięć trzyma tylko pełne wymiany kończące się turą asystenta — dzięki temu historia
    nie kończy się turą user (brak dwóch tur user z rzędu na kolejnej wiadomości).
    """
    llm = _ScriptedLLM(
        [
            LLMResponse(
                text="czę", blocks=({"type": "text", "text": "czę"},), stop_reason="max_tokens"
            )
        ]
    )

    result = AgentRuntime(llm, []).run_turn("pytanie")

    assert result.stop_reason == "max_tokens"
    assert result.reply == "czę"  # zwracamy to, co model zdążył napisać
    assert result.entries == ()  # niedomknięta tura → pusto (bez sieroty)


def test_run_turn_final_turn_carries_blocks_verbatim():
    """Zwykła odpowiedź końcowa: entries = user + assistant z blokami VERBATIM."""
    blocks = ({"type": "thinking", "signature": "S"}, {"type": "text", "text": "gotowe"})
    llm = _ScriptedLLM([LLMResponse(text="gotowe", blocks=blocks, stop_reason="end_turn")])

    result = AgentRuntime(llm, []).run_turn("pytanie")

    assert result.stop_reason == "end_turn"
    assert result.entries == (UserText("pytanie"), AssistantTurn("gotowe", (), blocks))


def test_run_turn_surfaces_thinking_summary_on_final_answer():
    """``AgentResult.thinking`` niesie podsumowanie rozumowania końcowej tury (summarized)."""
    llm = _ScriptedLLM(
        [
            LLMResponse(
                text="gotowe",
                thinking_text="Rozważam notatki mpwik.",
                blocks=({"type": "text", "text": "gotowe"},),
                stop_reason="end_turn",
            )
        ]
    )

    result = AgentRuntime(llm, []).run_turn("pytanie")

    assert result.thinking == "Rozważam notatki mpwik."
    assert result.reply == "gotowe"


def test_run_turn_accumulates_real_usage_across_tool_loop():
    """Design 2: ``AgentResult.usage`` = suma usage wszystkich wywołań; każda tura niesie swoje."""

    def search(query: str) -> dict:
        return {"count": 1}

    llm = _ScriptedLLM(
        [
            LLMResponse(
                tool_calls=(ToolCall("t1", "search_notes", {"query": "x"}),),
                usage=TokenUsage(input_tokens=100, output_tokens=10),
            ),
            LLMResponse(
                text="ok",
                stop_reason="end_turn",
                usage=TokenUsage(input_tokens=150, output_tokens=20),
            ),
        ]
    )

    result = AgentRuntime(llm, [_spec("search_notes", search)]).run_turn("q")

    assert result.usage == TokenUsage(input_tokens=250, output_tokens=30)  # suma dwóch wywołań
    assistants = [e for e in result.entries if isinstance(e, AssistantTurn)]
    assert assistants[0].usage == TokenUsage(input_tokens=100, output_tokens=10)
    assert assistants[1].usage == TokenUsage(input_tokens=150, output_tokens=20)


def test_run_turn_persists_paired_tool_cycle_entries():
    """Cykl tool_use utrwala SPAROWANE wpisy: user, assistant(+tool), tool, assistant."""

    def search(query: str) -> dict:
        return {"count": 1}

    assistant_blocks = ({"type": "text", "text": "szukam"},)
    llm = _ScriptedLLM(
        [
            LLMResponse(
                text="szukam",
                tool_calls=(ToolCall("t1", "search_notes", {"query": "mpwik"}),),
                blocks=assistant_blocks,
                stop_reason="tool_use",
            ),
            LLMResponse(text="Znalazłem 1.", stop_reason="end_turn"),
        ]
    )

    result = AgentRuntime(llm, [_spec("search_notes", search)]).run_turn("co z mpwik?")

    assert result.stop_reason == "end_turn"
    kinds = [type(e).__name__ for e in result.entries]
    assert kinds == ["UserText", "AssistantTurn", "ToolResults", "AssistantTurn"]
    # Tura z tool_use niesie bloki VERBATIM i żądanie narzędzia.
    first_assistant = result.entries[1]
    assert isinstance(first_assistant, AssistantTurn)
    assert first_assistant.blocks == assistant_blocks
    assert first_assistant.tool_calls[0].name == "search_notes"
    # Wynik narzędzia sparowany po turze asystenta.
    tool_entry = result.entries[2]
    assert isinstance(tool_entry, ToolResults)
    assert '"count": 1' in tool_entry.outputs[0].content
