"""Adapter outbound: klient Claude API implementujący port ``LLMClient`` (ADR 0008).

Importy ``anthropic`` (leniwy, extra ``agent``) i ``func_metadata`` żyją TU, w
adapterze — nie w rdzeniu. Adapter tłumaczy słownik domenowy ⇆ wiadomości/bloki
Anthropic i wyprowadza ``input_schema`` narzędzia z tej samej ``fn`` co drzwi MCP
(jeden generator, jeden podpis → schemat agenta zgodny z MCP). Klucz API czyta
wyłącznie ten adapter; rdzeń i runtime go nie widzą.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from workmate.core.errors import LLMError
from workmate.core.ports.llm import (
    AssistantTurn,
    LLMResponse,
    ToolCall,
    ToolResults,
    UserText,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from workmate.config import AgentSettings
    from workmate.core.application.tools import ToolSpec
    from workmate.core.ports.llm import TranscriptEntry


class AnthropicLLMClient:
    """``LLMClient`` nad Claude API.

    ``import anthropic`` jest w ``__init__`` (nie na poziomie modułu), więc sam
    import tego modułu nie wymaga extra ``agent`` — ale zbudowanie klienta już tak.
    Brak extra daje wtedy czytelny ``ImportError`` w punkcie składania (CLI łapie
    go i podpowiada instalację).
    """

    def __init__(self, settings: AgentSettings) -> None:
        import anthropic

        self._settings = settings
        self._client: Any = anthropic.Anthropic(api_key=settings.api_key or None)

    def complete(
        self,
        *,
        system: str,
        transcript: Sequence[TranscriptEntry],
        tools: Sequence[ToolSpec],
    ) -> LLMResponse:
        import anthropic

        try:
            message = self._client.messages.create(
                model=self._settings.model,
                max_tokens=self._settings.max_tokens,
                system=system,
                messages=_to_messages(transcript),
                tools=[_to_tool_def(spec) for spec in tools],
            )
        except anthropic.APIError as exc:
            raise LLMError(f"Błąd Claude API: {exc}") from exc
        return _from_message(message)


def _to_tool_def(spec: ToolSpec) -> dict[str, Any]:
    """Zbuduj definicję narzędzia Anthropic; ``input_schema`` z tej samej ``fn`` co MCP."""
    from mcp.server.fastmcp.utilities.func_metadata import func_metadata

    schema = func_metadata(spec.fn).arg_model.model_json_schema()
    return {"name": spec.name, "description": spec.description, "input_schema": schema}


def _to_messages(transcript: Sequence[TranscriptEntry]) -> list[dict[str, Any]]:
    """Zmapuj słownik domenowy na listę wiadomości Anthropic."""
    messages: list[dict[str, Any]] = []
    for entry in transcript:
        if isinstance(entry, UserText):
            messages.append({"role": "user", "content": entry.text})
        elif isinstance(entry, AssistantTurn):
            content: list[dict[str, Any]] = []
            if entry.text:
                content.append({"type": "text", "text": entry.text})
            for call in entry.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            messages.append({"role": "assistant", "content": content})
        elif isinstance(entry, ToolResults):
            results: list[dict[str, Any]] = []
            for output in entry.outputs:
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": output.call_id,
                    "content": output.content,
                }
                if output.is_error:
                    block["is_error"] = True
                results.append(block)
            messages.append({"role": "user", "content": results})
    return messages


def _from_message(message: Any) -> LLMResponse:
    """Zmapuj odpowiedź Anthropic na słownik domenowy (tekst + żądania narzędzi)."""
    text_parts: list[str] = []
    calls: list[ToolCall] = []
    for block in message.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
    return LLMResponse(text="".join(text_parts), tool_calls=tuple(calls))
