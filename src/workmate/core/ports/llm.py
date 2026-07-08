"""Port klienta LLM (Faza 2, M1 / ADR 0008) — kontrakt runtime'u agenta wobec modelu.

Słownik jest DOMENOWY (anty-korupcja): runtime mówi o turach rozmowy i wywołaniach
narzędzi, nie o blokach treści Anthropic. Adapter outbound (``AnthropicLLMClient``)
tłumaczy ten słownik na/z Claude API, więc wokabularz SDK nigdy nie wchodzi do
rdzenia (reguła ``core ↛ adapters``). Runtime testujemy atrapą ``LLMClient``,
bez sieci — analogicznie do atrap repozytoriów.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

    from workmate.core.application.tools import ToolSpec


@dataclass(frozen=True)
class ToolCall:
    """Żądanie wywołania narzędzia od modelu (id koreluje wynik z żądaniem)."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolOutput:
    """Wynik wykonania narzędzia zwracany modelowi w kolejnej turze."""

    call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class UserText:
    """Wpis transkryptu: tekst od użytkownika."""

    text: str


@dataclass(frozen=True)
class AssistantTurn:
    """Wpis transkryptu: tura modelu (tekst i/lub żądane wywołania narzędzi)."""

    text: str
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class ToolResults:
    """Wpis transkryptu: wyniki narzędzi wykonanych przez runtime."""

    outputs: tuple[ToolOutput, ...]


TranscriptEntry = UserText | AssistantTurn | ToolResults


@dataclass(frozen=True)
class LLMResponse:
    """Jedna tura modelu: tekst i/lub żądane wywołania narzędzi."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()

    @property
    def wants_tools(self) -> bool:
        """Czy model chce wywołać narzędzia (True), czy to już odpowiedź końcowa?"""
        return bool(self.tool_calls)


class LLMClient(Protocol):
    """Kontrakt: z (prompt systemowy, transkrypt, katalog narzędzi) → tura modelu."""

    def complete(
        self,
        *,
        system: str,
        transcript: Sequence[TranscriptEntry],
        tools: Sequence[ToolSpec],
    ) -> LLMResponse: ...
