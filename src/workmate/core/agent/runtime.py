"""Runtime agenta (Faza 2, M1 / ADR 0008; ADR 0011) — ograniczona pętla tool-use.

Zależy tylko od portu ``LLMClient`` i katalogu ``ToolSpec``; Anthropic i FastMCP tu
nie wchodzą, więc runtime testujemy atrapą LLM bez sieci. Pętla jest ograniczona
(``max_tool_iterations``) — chroni koszt i latencję. Błąd narzędzia wraca do modelu
jako ``ToolOutput`` (narzędzia zwracają ``{"error": ...}``, nie rzucają); wyjątek
nieznany z narzędzia wypływa jako defekt kodu.

``run_turn`` (ADR 0011) zwraca ``AgentResult`` z REPLAYOWALNYMI wpisami tej tury do
zapisu w pamięci: tura ucięta na ``max_tokens`` jest wykluczana z zapisu (jej
odtworzenie dałoby API 400). ``run`` to cienka nakładka zwracająca sam tekst.
"""

from __future__ import annotations

import inspect
import json
from typing import TYPE_CHECKING

from workmate.core.agent.prompt import SYSTEM_PROMPT
from workmate.core.domain.pricing import TokenUsage
from workmate.core.ports.llm import (
    AgentResult,
    AssistantTurn,
    ToolOutput,
    ToolResults,
    UserText,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from workmate.core.application.tools import ToolSpec
    from workmate.core.ports.llm import (
        Attachment,
        LLMClient,
        ToolCall,
        TranscriptEntry,
    )

_DEFAULT_MAX_TOOL_ITERATIONS = 8
# ``stop_reason`` sygnalizujący UCIĘCIE odpowiedzi (thinking + tekst dzielą max_tokens):
# tura niepełna, więc niereplayowalna (niepełny thinking/tool_use → API 400).
_TRUNCATED = "max_tokens"
_ITERATIONS_EXHAUSTED = "max_tool_iterations"


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

    def run(
        self,
        query: str,
        *,
        attachments: Sequence[Attachment] = (),
        history: Sequence[TranscriptEntry] = (),
        extra_tools: Sequence[ToolSpec] = (),
    ) -> str:
        """Zwróć sam tekst odpowiedzi — cienka nakładka na ``run_turn`` (drzwi bezstanowe)."""
        return self.run_turn(
            query, attachments=attachments, history=history, extra_tools=extra_tools
        ).reply

    def run_turn(
        self,
        query: str,
        *,
        attachments: Sequence[Attachment] = (),
        history: Sequence[TranscriptEntry] = (),
        extra_tools: Sequence[ToolSpec] = (),
    ) -> AgentResult:
        """Wykonaj turę: wołaj narzędzia w pętli i zwróć odpowiedź + wpisy DO ZAPISU.

        ``history`` to wcześniejsze tury bieżącej rozmowy (pamięć, ADR 0010) —
        poprzedzają nową wiadomość jako kontekst. Puste dla drzwi bezstanowych.
        INWARIANT ZAPISU (ADR 0011): zapisujemy TYLKO turę domkniętą końcową odpowiedzią
        asystenta. Tura, która nie dobiła do czystej odpowiedzi — ucięta na ``max_tokens``
        albo z wyczerpanym limitem iteracji — zwraca ``entries=()`` (nic do zapisu). Dzięki
        temu w pamięci są WYŁĄCZNIE pełne wymiany kończące się turą asystenta, więc historia
        zawsze alternuje poprawnie (kolejna wiadomość użytkownika nie tworzy dwóch tur user
        z rzędu) i żadna niereplayowalna tura (niepełny thinking/tool_use) nie trafia do API.
        Turę traktujemy jak przejściową porażkę: użytkownik dostaje częściową/zastępczą
        odpowiedź, a pamięć zostaje spójna (bez sieroty).
        """
        # ``extra_tools`` (ADR 0018): narzędzia dokładane per turę, np. katalog roboczy związany z
        # rozmową (scope domknięty w closurze). Scalamy z bazowym katalogiem TYLKO na to wywołanie
        # — runtime pozostaje współdzielony i bezstanowy, a izolacja scope jest per tura.
        catalog = (*self._catalog, *extra_tools)
        by_name = {**self._by_name, **{spec.name: spec for spec in extra_tools}}
        user_turn = UserText(query, tuple(attachments))
        transcript: list[TranscriptEntry] = [*history, user_turn]
        new_entries: list[TranscriptEntry] = [user_turn]
        last_text = ""
        # Realne ``usage`` sumowane po WSZYSTKICH wywołaniach API tej tury (pętla tool-use);
        # ``AgentResult.usage`` = koszt całej tury, a każda ``AssistantTurn`` niesie usage
        # swojego wywołania (Design 2 — do rozliczenia i do bramki rolloveru na ostatniej turze).
        run_usage = TokenUsage()
        for _ in range(self._max_tool_iterations):
            response = self._llm.complete(
                system=self._system_prompt, transcript=transcript, tools=catalog
            )
            run_usage = run_usage + response.usage
            last_text = response.text or last_text

            if response.stop_reason == _TRUNCATED:
                # Tura ucięta: NIE dispatchujemy (tool_use bywa niepełny) i NIC nie
                # zapisujemy (patrz inwariant wyżej). Zwracamy to, co model zdążył napisać.
                return AgentResult(
                    reply=response.text or last_text,
                    entries=(),
                    stop_reason=_TRUNCATED,
                    usage=run_usage,
                )

            if not response.wants_tools:
                assistant = AssistantTurn(response.text, (), response.blocks, usage=response.usage)
                new_entries.append(assistant)
                return AgentResult(
                    reply=response.text,
                    entries=tuple(new_entries),
                    stop_reason=response.stop_reason,
                    thinking=response.thinking_text,
                    usage=run_usage,
                )

            assistant = AssistantTurn(
                response.text, response.tool_calls, response.blocks, usage=response.usage
            )
            transcript.append(assistant)
            new_entries.append(assistant)
            results = ToolResults(tuple(self._dispatch(c, by_name) for c in response.tool_calls))
            transcript.append(results)
            new_entries.append(results)

        # Wyczerpany limit iteracji bez czystej odpowiedzi: nic nie zapisujemy (inwariant).
        return AgentResult(
            reply=last_text or "Przekroczono limit iteracji narzędzi bez odpowiedzi.",
            entries=(),
            stop_reason=_ITERATIONS_EXHAUSTED,
            usage=run_usage,
        )

    def _dispatch(self, call: ToolCall, by_name: dict[str, ToolSpec]) -> ToolOutput:
        spec = by_name.get(call.name)
        if spec is None:
            return ToolOutput(call.id, f"Nieznane narzędzie: {call.name}", is_error=True)
        # Argumenty pochodzą od modelu (dane niezaufane). Sprawdzamy TYLKO ich
        # wiązanie z sygnaturą (zła/brakująca/nadmiarowa nazwa) i zwracamy odzyskiwalny
        # błąd — model poprawi w kolejnej turze, pętla się nie wywraca. Właściwe
        # wywołanie jest POZA ``try``, więc wyjątek z ciała narzędzia (defekt kodu)
        # wypływa głośno, zgodnie z kontraktem rdzenia; błędy domenowe narzędzie łapie
        # samo i zwraca ``{"error": ...}``.
        try:
            inspect.signature(spec.fn).bind(**call.arguments)
        except TypeError as exc:
            return ToolOutput(
                call.id,
                f"Nieprawidłowe argumenty narzędzia {call.name}: {exc}",
                is_error=True,
            )
        result = spec.fn(**call.arguments)
        return ToolOutput(call.id, json.dumps(result, ensure_ascii=False, default=str))
