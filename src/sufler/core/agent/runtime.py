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
import logging
from typing import TYPE_CHECKING, Any, get_type_hints

from pydantic import TypeAdapter, ValidationError

from sufler.core.agent.prompt import STATIC_PROMPT, budget_notice, system_blocks
from sufler.core.domain.pricing import TokenUsage
from sufler.core.ports.llm import (
    AgentResult,
    AssistantTurn,
    ToolOutput,
    ToolResults,
    UserText,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from sufler.core.application.tools import ToolSpec
    from sufler.core.domain.trust import TrustClass
    from sufler.core.ports.llm import (
        Attachment,
        AttachmentQueue,
        LLMClient,
        ToolCall,
        TranscriptEntry,
    )

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOOL_ITERATIONS = 8
# ``stop_reason`` sygnalizujący UCIĘCIE odpowiedzi (thinking + tekst dzielą max_tokens):
# tura niepełna, więc niereplayowalna (niepełny thinking/tool_use → API 400).
_TRUNCATED = "max_tokens"
_ITERATIONS_EXHAUSTED = "max_tool_iterations"
# Od ilu pozostałych rund modelowi mówimy, ile ich zostało (ADR 0068 §6). Dwie, bo jedna runda
# na samo domknięcie odpowiedzi to za późno na zmianę planu: model, który właśnie zaczął serię
# wywołań, ma zdążyć przejść na „odpowiadam tym, co mam".
_BUDGET_WARNING_ROUNDS = 2


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

    @property
    def catalog(self) -> tuple[ToolSpec, ...]:
        """Katalog BAZOWY tego runtime'u — do odczytu metadanych narzędzi (ADR 0073).

        Drzwi znają wyłącznie narzędzia, które same dokładają per turę (``extra_tools``);
        reszta powierzchni — z ``Activity`` włącznie — mieszka tutaj. Bez tego okna konsument
        metadanej ``ToolSpec.taints`` musiałby ją POWTÓRZYĆ listą napisów, czyli dokładnie tym,
        co ADR 0073 zdejmuje. Krotka, nie lista: okno jest do czytania.
        """
        return tuple(self._catalog)

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
        attachment_queue: AttachmentQueue | None = None,
        trust_nonce: str = "",
        trust: TrustClass = "T1",
    ) -> AgentResult:
        """Wykonaj turę: wołaj narzędzia w pętli i zwróć odpowiedź + wpisy DO ZAPISU.

        ``trust_nonce`` (ADR 0066) włącza koperty na treści obcej; runtime tylko go PRZENOSI
        do adaptera — nie generuje go i nie wie, co znaczy. Pusty = etykiety wyłączone.

        ``attachment_queue`` (ADR 0064) to kolejka plików, które narzędzie ``File`` materializuje
        w trakcie tury. Runtime jej nie wypełnia — tylko OPRÓŻNIA po każdej rundzie wywołań i
        dokłada zabrane pliki do ``ToolResults``. Drzwi tworzą ją per tura razem z narzędziem
        (wspólna closure), więc runtime zostaje bezstanowy i nie wie nic o materializacji.

        ``audit`` (ADR 0067) to rejestrator per turę: dla KAŻDEGO wywołania narzędzia dostaje
        ``(nazwa, argumenty, status)``. ``None`` → brak audytu (dawne zachowanie). Kontekst tury
        (pseudonim nadawcy/rozmowy, klasa zaufania) jest domknięty PO STRONIE drzwi — runtime widzi
        tylko wąski callback i pozostaje niezależny od pseudonimizacji i magazynu.
        To JEDYNA droga, którą fakt o wywołaniu narzędzia wychodzi z tury niezależnie od tego,
        czy tura dobiła do zapisu — drzwi wieszają na niej także lepką skazę rozmowy (ADR 0066),
        bo ``entries=()`` niżej gubi wszystko, co czytane jest z WYNIKU tury.

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
        UWAGA dla każdej nowej ścieżki wyjścia z pętli: pusty ``entries`` znaczy „nic do
        ZAPISU", nie „nic się nie wydarzyło". Narzędzia zdążyły pobiec i ich skutki uboczne
        zostają, więc faktu o nich nie wolno wywodzić z wyniku tury — od tego jest ``audit``,
        wołany w chwili wywołania (patrz wyżej).
        """
        # ``extra_tools`` (ADR 0018): narzędzia dokładane per turę, np. katalog roboczy związany z
        # rozmową (scope domknięty w closurze). Scalamy z bazowym katalogiem TYLKO na to wywołanie
        # — runtime pozostaje współdzielony i bezstanowy, a izolacja scope jest per tura.
        catalog = (*self._catalog, *extra_tools)
        by_name = {**self._by_name, **{spec.name: spec for spec in extra_tools}}
        # Klasa pochodzenia tury (ADR 0066) nadana przez DRZWI — runtime jej nie wylicza
        # i nie zna nadawcy; niesie ją dalej, bo to ona ląduje w pamięci i w audycie.
        user_turn = UserText(query, tuple(attachments), trust)
        transcript: list[TranscriptEntry] = [*history, user_turn]
        new_entries: list[TranscriptEntry] = [user_turn]
        last_text = ""
        # Realne ``usage`` sumowane po WSZYSTKICH wywołaniach API tej tury (pętla tool-use);
        # ``AgentResult.usage`` = koszt całej tury, a każda ``AssistantTurn`` niesie usage
        # swojego wywołania (Design 2 — do rozliczenia i do bramki rolloveru na ostatniej turze).
        run_usage = TokenUsage()
        for iteration in range(self._max_tool_iterations):
            # Bloki systemowe składamy w pętli, bo zmienia się w nich JEDNO zdanie: budżet
            # pozostałych rund (ADR 0068 §6). Jedzie ono do DRUGIEGO bloku — nagłówka sesji —
            # który z definicji leży poza cache'owanym prefiksem ``tools+system``, więc korpus
            # zostaje bajt w bajt ten sam. Do wyczerpania limitu model dostawał ciszę, a potem
            # tracił całą turę razem z wiadomością użytkownika (inwariant ADR 0011 bez zmian).
            system = system_blocks(
                self._system_prompt,
                _header_with_budget(session_header, self._max_tool_iterations - iteration),
            )
            response = self._llm.complete(
                system=system, transcript=transcript, tools=catalog, trust_nonce=trust_nonce
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
            outputs = tuple(self._dispatch(c, by_name, audit) for c in response.tool_calls)
            # Pliki zmaterializowane przez ``File`` w TEJ rundzie (ADR 0064). Zabieramy je po
            # dispatchu, więc jadą jako bloki obok wyników narzędzi, w tej samej wiadomości
            # ``user`` — i model widzi je od razu, w tej samej turze, a nie dopiero gdy odezwie
            # się człowiek. Bez kolejki: dawne zachowanie co do bajta.
            results = ToolResults(
                outputs, attachment_queue.drain() if attachment_queue is not None else ()
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
        # wyjątek propaguje się dalej zgodnie z kontraktem rdzenia.
        status = "error"
        try:
            result = spec.fn(**arguments)
            status = "error" if isinstance(result, dict) and "error" in result else "ok"
            return ToolOutput(call.id, json.dumps(result, ensure_ascii=False, default=str))
        finally:
            # Rejestrator MA być best-effort, ale „best-effort" to własność wołania, nie obietnica
            # implementacji. Wyjątek stąd padłby w ``finally``, czyli ZASTĄPIŁby wynik narzędzia
            # (albo jego wyjątek) swoim własnym: udana operacja wracałaby do modelu jako awaria
            # dziennika, a prawdziwa przyczyna znikała. Osłona jest tu, bo tylko tu widać, co
            # traci się przy jej braku.
            try:
                audit(call.name, arguments, status)
            except Exception:
                logger.warning(
                    "Nie udało się zapisać wpisu audytu dla narzędzia %s — wynik tury zostaje",
                    call.name,
                    exc_info=True,
                )


def _header_with_budget(session_header: str, remaining_rounds: int) -> str:
    """Nagłówek sesji, a przy końcu budżetu — plus zdanie o pozostałych rundach (ADR 0068 §6).

    Poza progiem zwraca nagłówek NIETKNIĘTY (ten sam obiekt), więc typowa tura jedzie dokładnie
    tak jak dotąd. Sygnał wchodzi do nagłówka, a nie do transkryptu: transkrypt jest zapisywany
    i odtwarzany, a zdanie o budżecie jest prawdziwe wyłącznie w tej jednej rundzie.
    """
    if remaining_rounds > _BUDGET_WARNING_ROUNDS:
        return session_header
    notice = budget_notice(remaining_rounds)
    return f"{session_header}\n\n{notice}" if session_header else notice


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
