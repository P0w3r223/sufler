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
from typing import TYPE_CHECKING, Any, get_type_hints

from pydantic import TypeAdapter, ValidationError

from workmate.core.agent.prompt import STATIC_PROMPT, system_blocks
from workmate.core.domain.pricing import TokenUsage
from workmate.core.ports.llm import (
    AgentResult,
    AssistantTurn,
    ToolOutput,
    ToolResults,
    UserText,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

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
        system_prompt: str = STATIC_PROMPT,
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
        session_header: str = "",
    ) -> str:
        """Zwróć sam tekst odpowiedzi — cienka nakładka na ``run_turn`` (drzwi bezstanowe)."""
        return self.run_turn(
            query,
            attachments=attachments,
            history=history,
            extra_tools=extra_tools,
            session_header=session_header,
        ).reply

    def run_turn(
        self,
        query: str,
        *,
        attachments: Sequence[Attachment] = (),
        history: Sequence[TranscriptEntry] = (),
        extra_tools: Sequence[ToolSpec] = (),
        session_header: str = "",
        audit: Callable[[str, Mapping[str, Any], str], None] | None = None,
    ) -> AgentResult:
        """Wykonaj turę: wołaj narzędzia w pętli i zwróć odpowiedź + wpisy DO ZAPISU.

        ``audit`` (ADR 0067) to rejestrator per turę: dla KAŻDEGO wywołania narzędzia dostaje
        ``(nazwa, argumenty, status)``. ``None`` → brak audytu (dawne zachowanie). Kontekst tury
        (pseudonim nadawcy/rozmowy, klasa zaufania) jest domknięty PO STRONIE drzwi — runtime widzi
        tylko wąski callback i pozostaje niezależny od pseudonimizacji i magazynu.

        ``history`` to wcześniejsze tury bieżącej rozmowy (pamięć, ADR 0010) —
        poprzedzają nową wiadomość jako kontekst. Puste dla drzwi bezstanowych.
        ``session_header`` (ADR 0056) to DRUGI blok systemowy tej tury — data i rozmowa.
        Podają go drzwi, bo niosą zegar; puste zachowuje dawny, jednoblokowy kształt.
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
        # Bloki systemowe składamy RAZ na turę, nie w pętli: w obrębie jednej tury data i
        # rozmowa są stałe, a powtórne składanie tylko rozmnażałoby okazje do rozjazdu.
        system = system_blocks(self._system_prompt, session_header)
        user_turn = UserText(query, tuple(attachments))
        transcript: list[TranscriptEntry] = [*history, user_turn]
        new_entries: list[TranscriptEntry] = [user_turn]
        last_text = ""
        # Realne ``usage`` sumowane po WSZYSTKICH wywołaniach API tej tury (pętla tool-use);
        # ``AgentResult.usage`` = koszt całej tury, a każda ``AssistantTurn`` niesie usage
        # swojego wywołania (Design 2 — do rozliczenia i do bramki rolloveru na ostatniej turze).
        run_usage = TokenUsage()
        for _ in range(self._max_tool_iterations):
            response = self._llm.complete(system=system, transcript=transcript, tools=catalog)
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
            results = ToolResults(
                tuple(self._dispatch(c, by_name, audit) for c in response.tool_calls)
            )
            transcript.append(results)
            new_entries.append(results)

        # Wyczerpany limit iteracji bez czystej odpowiedzi: nic nie zapisujemy (inwariant).
        return AgentResult(
            reply=last_text or "Przekroczono limit iteracji narzędzi bez odpowiedzi.",
            entries=(),
            stop_reason=_ITERATIONS_EXHAUSTED,
            usage=run_usage,
        )

    def _dispatch(
        self,
        call: ToolCall,
        by_name: dict[str, ToolSpec],
        audit: Callable[[str, Mapping[str, Any], str], None] | None = None,
    ) -> ToolOutput:
        spec = by_name.get(call.name)
        if spec is None:
            # Wywołanie ODRZUCONE przed uruchomieniem narzędzia (nieznana nazwa) — cenny ślad
            # audytu: próba sięgnięcia po narzędzie spoza katalogu (np. za bramką zdolności,
            # powłoka OFF). Argumenty surowe od modelu; ``project_arguments`` w rejestratorze je
            # zredaguje, więc treść nie wycieknie mimo braku koercji.
            if audit is not None:
                audit(call.name, call.arguments, "rejected")
            return ToolOutput(call.id, f"Nieznane narzędzie: {call.name}", is_error=True)
        # Argumenty pochodzą od modelu (dane niezaufane). Sprawdzamy wiązanie z sygnaturą
        # ORAZ typy, i zwracamy odzyskiwalny błąd — model poprawi w kolejnej turze, pętla
        # się nie wywraca. Właściwe wywołanie jest POZA ``try``, więc wyjątek z ciała
        # narzędzia (defekt kodu) wypływa głośno, zgodnie z kontraktem rdzenia; błędy
        # domenowe narzędzie łapie samo i zwraca ``{"error": ...}``.
        try:
            arguments = _coerce_arguments(spec.fn, call.arguments)
        except (TypeError, ValidationError) as exc:
            # Odrzucone na walidacji argumentów — też ślad „narzędzie X zawiodło przed wykonaniem".
            # Argumenty surowe (koercja padła); rejestrator je redaguje.
            if audit is not None:
                audit(call.name, call.arguments, "rejected")
            return ToolOutput(
                call.id,
                f"Nieprawidłowe argumenty narzędzia {call.name}: {exc}",
                is_error=True,
            )
        if audit is None:
            result = spec.fn(**arguments)
            return ToolOutput(call.id, json.dumps(result, ensure_ascii=False, default=str))
        # Audyt per wywołanie (ADR 0067): rejestrujemy nazwę, ZREDAGOWANE argumenty i status w
        # ``finally``, więc wpis powstaje TAKŻE, gdy narzędzie rzuci defekt (status "error"), a sam
        # wyjątek propaguje się dalej zgodnie z kontraktem rdzenia. Rejestrator jest best-effort
        # (łapie własne błędy), więc wołamy go bez osłony — nie może zamaskować wyniku tury.
        status = "error"
        try:
            result = spec.fn(**arguments)
            status = "error" if isinstance(result, dict) and "error" in result else "ok"
            return ToolOutput(call.id, json.dumps(result, ensure_ascii=False, default=str))
        finally:
            audit(call.name, arguments, status)


# ``*args``/``**kwargs`` niosą krotkę/słownik, a adnotacja opisuje POJEDYNCZY element —
# koercja rozminęłaby się z kształtem. Katalog ich nie używa; gdyby zaczął, lepiej
# przepuścić wartość niż ją zepsuć.
_VARIADIC = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)


def _coerce_arguments(fn: Callable[..., Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Zwiąż argumenty od modelu z sygnaturą i SKOERUJ je do typów z adnotacji.

    Model widzi schemat wyprowadzony z tych samych adnotacji, a JSON nie zna typów
    Pythona: ``date`` jedzie do niego jako ``{"type": "string", "format": "date"}``, więc
    model SŁUSZNIE przysyła napis. Bez koercji napis trafiał wprost do arytmetyki dat
    i wywracał pętlę ``TypeError``-em, którego koperta narzędzi nie łapie — a wyjątek leci
    PRZED oznaczeniem wiadomości jako obsłużonej, więc ta sama wiadomość mieliła się
    w kółko (pełna tura LLM za każdym razem) i blokowała kanał aż do restartu.

    Walidujemy CZYSTYM pydantikiem, nie ``func_metadata`` z SDK, bo rdzeń nie importuje
    SDK. Oba widoki wywodzą się z tych samych adnotacji, więc schemat pokazany modelowi
    i walidacja tutaj mówią o tym samym.

    ``TypeError`` (zła/brakująca/nadmiarowa nazwa) i ``ValidationError`` (zły typ) wołający
    zamienia na odzyskiwalny wynik narzędzia — model poprawia się w kolejnej turze.
    """
    signature = inspect.signature(fn)
    bound = signature.bind(**arguments)
    hints = get_type_hints(fn)
    coerced: dict[str, Any] = {}
    for name, value in bound.arguments.items():
        annotation = hints.get(name)
        if annotation is None or signature.parameters[name].kind in _VARIADIC:
            coerced[name] = value
            continue
        coerced[name] = TypeAdapter(annotation).validate_python(value)
    return coerced
