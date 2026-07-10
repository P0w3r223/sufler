"""Port klienta LLM (Faza 2, M1 / ADR 0008; rozszerzony w ADR 0011) — kontrakt
runtime'u agenta wobec modelu.

Słownik jest DOMENOWY (anty-korupcja): runtime mówi o turach rozmowy i wywołaniach
narzędzi, nie o blokach treści Anthropic. Adapter outbound (``AnthropicLLMClient``)
tłumaczy ten słownik na/z Claude API, więc wokabularz SDK nigdy nie wchodzi do
rdzenia (reguła ``core ↛ adapters``). Runtime testujemy atrapą ``LLMClient``,
bez sieci — analogicznie do atrap repozytoriów.

Jeden świadomy wyjątek od anty-korupcji (ADR 0011): pola ``blocks`` niosą NIEPRZEZROCZYSTĄ
tablicę bloków treści dostawcy (``tuple[Mapping[str, Any], ...]``), żeby pamięć rozmów
mogła odtwarzać historię bajt-w-bajt (bloki ``thinking`` z ``signature`` MUSZĄ wrócić
niezmienione). Rdzeń tych bloków NIGDY nie interpretuje — tylko je przenosi
(opaque-cursor). Wiedza o ich kształcie żyje wyłącznie w adapterze Anthropic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
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
    """Wpis transkryptu: tura modelu (tekst i/lub żądane wywołania narzędzi).

    ``blocks`` (ADR 0011) to VERBATIM tablica bloków treści dostawcy dla tej tury
    (thinking + text + tool_use, w oryginalnej kolejności). Gdy niepusta, adapter
    odsyła ją bajt-w-bajt; gdy pusta (atrapy, wiersze legacy), adapter składa treść
    z ``text`` i ``tool_calls`` (kompatybilność wsteczna).
    """

    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    blocks: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class RawTurn:
    """Wpis transkryptu: odtworzona tura z pamięci — NIEPRZEZROCZYSTE bloki (ADR 0011).

    Nośnik historii: ``blocks`` to verbatim tablica bloków (dla ``assistant`` —
    bloki dostawcy z ``signature``). Rdzeń jej nie interpretuje; adapter odsyła jako
    ``{"role": role, "content": blocks}`` bez filtrowania ani zmiany kolejności.
    """

    role: str
    blocks: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class ToolResults:
    """Wpis transkryptu: wyniki narzędzi wykonanych przez runtime."""

    outputs: tuple[ToolOutput, ...]


TranscriptEntry = UserText | AssistantTurn | RawTurn | ToolResults


@dataclass(frozen=True)
class LLMResponse:
    """Jedna tura modelu: tekst, żądane narzędzia oraz VERBATIM bloki i ``stop_reason``.

    ``blocks`` (ADR 0011) to nieprzezroczysta tablica bloków treści tury (thinking z
    ``signature``, text, tool_use) do bezstratnego zapisu i odtworzenia. ``stop_reason``
    steruje pętlą runtime (``tool_use`` / ``end_turn`` / ``max_tokens`` / ``refusal``).
    """

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    blocks: tuple[Mapping[str, Any], ...] = ()
    stop_reason: str = ""
    # Podsumowanie rozumowania z bloków ``thinking`` (gdy ``display=summarized``); trzymane
    # OSOBNO od ``text``, by NIE wchodziło do projekcji FTS ani do szacunku tokenów (ADR 0011).
    thinking_text: str = ""

    @property
    def wants_tools(self) -> bool:
        """Czy model chce wywołać narzędzia (True), czy to już odpowiedź końcowa?"""
        return bool(self.tool_calls)


@dataclass(frozen=True)
class AgentResult:
    """Wynik jednej tury runtime'u (ADR 0011): odpowiedź + tury DO ZAPISU + ``stop_reason``.

    ``reply`` to tekst dla użytkownika. ``entries`` to NOWE wpisy transkryptu tej tury
    (wiadomość użytkownika + tury assistant/tool), już przefiltrowane do REPLAYOWALNYCH
    — tura ucięta na ``max_tokens`` albo wisząca ``tool_use`` jest wykluczana, bo jej
    odtworzenie dałoby API 400. Drzwi zapisują ``entries`` na ślepo.
    """

    reply: str
    entries: tuple[TranscriptEntry, ...] = ()
    stop_reason: str = ""
    # Podsumowanie rozumowania końcowej tury (gdy ``display=summarized``) — do pokazania na
    # zaufanych drzwiach (CLI). Puste, gdy myślenie wyłączone albo tura ucięta/bez odpowiedzi.
    thinking: str = ""


class LLMClient(Protocol):
    """Kontrakt: z (prompt systemowy, transkrypt, katalog narzędzi) → tura modelu."""

    def complete(
        self,
        *,
        system: str,
        transcript: Sequence[TranscriptEntry],
        tools: Sequence[ToolSpec],
    ) -> LLMResponse: ...
