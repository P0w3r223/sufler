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
    RawTurn,
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

        messages = _to_messages(transcript)
        tool_defs = [_to_tool_def(spec) for spec in tools]

        try:
            # STREAMING (ADR 0011, rewizja): przy dużym ``max_tokens`` (domyślnie 128k —
            # pełny sufit modelu) tryb non-streaming (``messages.create``) jest ODRZUCANY
            # przez SDK (szacowany czas > limitu, zrywane bezczynne połączenie). Streaming
            # tego nie ma; ``get_final_message`` zwraca tę samą ``Message`` co ``create``
            # (pełna lista bloków + ``stop_reason``), więc ``_from_message`` działa bez zmian.
            with self._client.messages.stream(
                model=self._settings.model,
                max_tokens=self._settings.max_tokens,
                system=system,
                # Adaptive thinking (ADR 0011): w Sonnet 5 to jedyny tryb „on" — model
                # sam decyduje, ile myśleć. Bloki ``thinking`` (z ``signature``) są
                # przechwytywane w ``_from_message`` i odsyłane VERBATIM w ``_to_messages``,
                # więc tura z ``tool_use`` nie jest już odrzucana (400). ``display=summarized``
                # każe modelowi zwrócić CZYTELNE podsumowanie rozumowania (domyślnie ``omitted``
                # = pusty tekst; ``signature`` i tak obecna do round-tripu). Dokładamy je tylko
                # dla ``adaptive`` — przy ``disabled`` (``WORKMATE_AGENT_THINKING=disabled``)
                # myślenia nie ma, więc pole jest bez znaczenia (patrz ``_thinking_config``).
                thinking=_thinking_config(self._settings.thinking_type),
                messages=messages,
                tools=tool_defs,
            ) as stream:
                message = stream.get_final_message()
        except anthropic.APIError as exc:
            raise LLMError(f"Błąd Claude API: {exc}") from exc
        return _from_message(message)


def _thinking_config(thinking_type: str) -> dict[str, str]:
    """Parametr ``thinking`` dla Claude API; ``display=summarized`` tylko dla ``adaptive``.

    ``summarized`` włącza czytelne podsumowanie rozumowania (domyślnie ``omitted`` = pusty
    tekst thinking). Dla ``disabled`` myślenia nie ma, więc ``display`` byłby bez znaczenia
    (i potencjalnie odrzucony) — pomijamy go, zachowując konfigurowalność trybu.
    """
    if thinking_type == "adaptive":
        return {"type": "adaptive", "display": "summarized"}
    return {"type": thinking_type}


def _to_tool_def(spec: ToolSpec) -> dict[str, Any]:
    """Zbuduj definicję narzędzia Anthropic; ``input_schema`` z tej samej ``fn`` co MCP."""
    from mcp.server.fastmcp.utilities.func_metadata import func_metadata

    schema = func_metadata(spec.fn).arg_model.model_json_schema()
    return {"name": spec.name, "description": spec.description, "input_schema": schema}


def _to_messages(transcript: Sequence[TranscriptEntry]) -> list[dict[str, Any]]:
    """Zmapuj słownik domenowy na listę wiadomości Anthropic.

    Tury z blokami (``RawTurn`` z pamięci, ``AssistantTurn`` z bieżącego przebiegu)
    odsyłamy VERBATIM — bez filtrowania i bez zmiany kolejności bloków (thinking MUSI
    poprzedzać ``tool_use`` i wrócić z niezmienioną ``signature``, inaczej API 400).
    ``AssistantTurn`` bez bloków (atrapy, wiersze legacy) składamy z ``text``/``tool_calls``.
    """
    messages: list[dict[str, Any]] = []
    for entry in transcript:
        if isinstance(entry, UserText):
            messages.append({"role": "user", "content": entry.text})
        elif isinstance(entry, RawTurn):
            messages.append({"role": entry.role, "content": [dict(b) for b in entry.blocks]})
        elif isinstance(entry, AssistantTurn):
            if entry.blocks:
                content: list[dict[str, Any]] = [dict(b) for b in entry.blocks]
            else:
                content = []
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
    """Zmapuj odpowiedź Anthropic na słownik domenowy (ADR 0011).

    Bloki treści przechwytujemy VERBATIM (``model_dump(mode="json")``) w oryginalnej
    kolejności — łącznie z ``thinking``/``redacted_thinking`` i ich ``signature`` — żeby
    pamięć mogła odesłać je bajt-w-bajt (API odrzuca bloki ZMODYFIKOWANE, nie odczytane).
    Z tych samych bloków wyprowadzamy pola semantyczne: ``text`` (konkatenacja bloków
    ``text``, bez thinking) steruje projekcją do FTS i odpowiedzią; ``thinking_text``
    (konkatenacja bloków ``thinking`` — podsumowanie, gdy ``display=summarized``) służy do
    pokazania rozumowania; ``tool_calls`` steruje dispatchem. ``stop_reason`` steruje pętlą
    (``max_tokens`` = tura ucięta, niereplayowalna).
    """
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    calls: list[ToolCall] = []
    blocks: list[dict[str, Any]] = []
    for block in message.content:
        blocks.append(block.model_dump(mode="json"))
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "thinking":
            # ``display=summarized`` → czytelne podsumowanie; ``omitted`` → pusty tekst.
            # Osobno od ``text`` (nie wchodzi do FTS ani do szacunku tokenów — ADR 0011).
            thinking_parts.append(block.thinking)
        elif block.type == "tool_use":
            calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
    return LLMResponse(
        text="".join(text_parts),
        thinking_text="".join(thinking_parts),
        tool_calls=tuple(calls),
        blocks=tuple(blocks),
        stop_reason=message.stop_reason or "",
    )
