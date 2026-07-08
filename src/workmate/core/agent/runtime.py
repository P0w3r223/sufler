"""Runtime agenta (Faza 2, M1 / ADR 0008) — ograniczona pętla tool-use nad katalogiem.

Zależy tylko od portu ``LLMClient`` i katalogu ``ToolSpec``; Anthropic i FastMCP tu
nie wchodzą, więc runtime testujemy atrapą LLM bez sieci. Pętla jest ograniczona
(``max_tool_iterations``) — chroni koszt i latencję. Błąd narzędzia wraca do modelu
jako ``ToolOutput`` (narzędzia zwracają ``{"error": ...}``, nie rzucają); wyjątek
nieznany z narzędzia wypływa jako defekt kodu.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import ValidationError

from workmate.core.agent.prompt import SYSTEM_PROMPT
from workmate.core.ports.llm import (
    AssistantTurn,
    ToolOutput,
    ToolResults,
    UserText,
)

if TYPE_CHECKING:
    from workmate.core.application.tools import ToolSpec
    from workmate.core.ports.llm import LLMClient, ToolCall, TranscriptEntry

_DEFAULT_MAX_TOOL_ITERATIONS = 8


class AgentRuntime:
    """Pętla agenta: model wybiera narzędzia, runtime je wykonuje i składa odpowiedź."""

    def __init__(
        self,
        llm: LLMClient,
        catalog: list[ToolSpec],
        *,
        system_prompt: str = SYSTEM_PROMPT,
        max_tool_iterations: int = _DEFAULT_MAX_TOOL_ITERATIONS,
    ) -> None:
        self._llm = llm
        self._catalog = catalog
        self._by_name = {spec.name: spec for spec in catalog}
        self._system_prompt = system_prompt
        self._max_tool_iterations = max_tool_iterations

    def run(self, query: str) -> str:
        """Zwróć odpowiedź na zapytanie w języku naturalnym, wołając narzędzia w pętli."""
        transcript: list[TranscriptEntry] = [UserText(query)]
        last_text = ""
        for _ in range(self._max_tool_iterations):
            response = self._llm.complete(
                system=self._system_prompt, transcript=transcript, tools=self._catalog
            )
            last_text = response.text or last_text
            if not response.wants_tools:
                return response.text
            transcript.append(AssistantTurn(response.text, response.tool_calls))
            outputs = tuple(self._dispatch(call) for call in response.tool_calls)
            transcript.append(ToolResults(outputs))
        return last_text or "Przekroczono limit iteracji narzędzi bez odpowiedzi."

    def _dispatch(self, call: ToolCall) -> ToolOutput:
        spec = self._by_name.get(call.name)
        if spec is None:
            return ToolOutput(call.id, f"Nieznane narzędzie: {call.name}", is_error=True)
        # Argumenty pochodzą od modelu (dane niezaufane). Drzwi MCP walidują je
        # schematem FastMCP przed wywołaniem; agent nie — więc zła/brakująca nazwa
        # albo niepoprawny typ dają błąd wiązania. Zwracamy go jako ODZYSKIWALNY
        # wynik narzędzia (model poprawi w kolejnej turze), zamiast wywracać całe
        # zapytanie. Domenowe błędy narzędzie łapie samo i zwraca ``{"error": ...}``.
        try:
            result = spec.fn(**call.arguments)
        except (TypeError, ValidationError) as exc:
            return ToolOutput(
                call.id,
                f"Nieprawidłowe argumenty narzędzia {call.name}: {exc}",
                is_error=True,
            )
        return ToolOutput(call.id, json.dumps(result, ensure_ascii=False, default=str))
