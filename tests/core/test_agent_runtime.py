"""Testy runtime'u agenta (Faza 2, M1) — atrapa ``LLMClient``, bez sieci.

Runtime zależy tylko od portu LLM i katalogu narzędzi, więc scenariusze pętli
tool-use sprawdzamy atrapą zwracającą zaplanowane odpowiedzi (jak atrapy repo w
``conftest.py``).
"""
from __future__ import annotations

from workmate.core.agent.runtime import AgentRuntime
from workmate.core.application.tools import ToolSpec
from workmate.core.ports.llm import LLMResponse, ToolCall, ToolResults


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
