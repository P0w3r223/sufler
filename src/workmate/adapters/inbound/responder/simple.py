"""Respondery bez runtime'u agenta: echo, gołe wywołanie modelu, zapis notatki, odporność."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from workmate.core.agent.prompt import build_session_header
from workmate.core.errors import WorkMateError

if TYPE_CHECKING:
    from collections.abc import Callable

    from workmate.core.agent.runtime import AgentRuntime
    from workmate.core.application.services import NotesWriteService

from workmate.adapters.inbound.responder.protocols import InboundMessage, Responder
from workmate.adapters.inbound.responder.transcript import _utcnow

logger = logging.getLogger(__name__)


class EchoResponder:
    """Spike: potwierdza odbiór, nie dotykając rdzenia WorkMate."""

    async def respond(self, message: InboundMessage) -> str:
        return f"Odebrałem notatkę: {message.text}"


class RuntimeResponder:
    """Szew M1→drzwi (ADR 0008): odpowiedź składa runtime agenta rdzenia.

    Wpięcie to jedna linia w entry-poincie drzwi (``EchoResponder()`` →
    ``RuntimeResponder(runtime)``); handler i wiring bez zmian. ``AgentRuntime.run``
    jest synchroniczny (woła Claude API), więc uruchamiamy go w wątku puli, żeby nie
    blokować pętli zdarzeń drzwi async. Katalog runtime'u dla mniej zaufanych drzwi
    (Teams) budujemy BEZ ``write_service`` (ADR 0006) — agent czyta, ale
    nie zapisuje.
    """

    def __init__(self, runtime: AgentRuntime, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self._runtime = runtime
        # Zegar wstrzykiwany jak w ``ConversationalResponder`` — nagłówek sesji (ADR 0056)
        # niesie datę, a rdzeń zegara nie woła.
        self._clock = clock

    async def respond(self, message: InboundMessage) -> str:
        loop = asyncio.get_running_loop()
        header = build_session_header(self._clock(), thread=message.conversation_id)
        return await loop.run_in_executor(
            None,
            lambda: self._runtime.run(
                message.text, attachments=message.attachments, session_header=header
            ),
        )


class SaveNoteResponder:
    """STUB (ADR 0008): responder zapisujący wiadomość jako notatkę przez ``save_note``.

    Świadomie NIEWPIĘTY: drzwi asynchroniczne (Teams) są mniej zaufane
    (ADR 0006), więc bezpośredni zapis z nich wymaga osobnej decyzji (bramka zapisu
    per drzwi + parsowanie wiadomości w ``NoteMetadata``). Zostawiony jako punkt
    szwu — realizacja to kolejny krok M3/M4, nie spike.
    """

    def __init__(self, write_service: NotesWriteService) -> None:
        self._write_service = write_service

    async def respond(self, message: InboundMessage) -> str:
        raise NotImplementedError(
            "SaveNoteResponder to stub — zapis z drzwi wymaga decyzji bramkowania (ADR 0006/0008)."
        )


class SafeResponder:
    """Dekorator responder'a: łagodna degradacja przy błędach (odporność drzwi async).

    Owija dowolny ``Responder`` i łapie błędy, żeby wdrożony bot nie odpowiadał ciszą
    ani tracebackiem, gdy runtime/narzędzie/infrastruktura zawiedzie:

    - ``WorkMateError`` (LLM, repozytorium, zapis) — błąd oczekiwany (np. przejściowy
      błąd Claude API): log WARNING + przyjazny komunikat.
    - dowolny inny wyjątek — defekt kodu: log z pełnym tracebackiem (``exception``),
      ale i tak zwracamy komunikat zamiast wywracać proces bota (jedna zła tura nie
      kładzie usługi). Błąd NIE jest połykany po cichu — ląduje w logu ze szczegółami.

    Analogicznie do granicy MCP (która zamienia błąd na ``{"error": ...}``) — ten szew
    daje tę granicę drzwiom async (Teams). Kontekst (nadawca, rozmowa) w logu.
    """

    _FALLBACK = "Przepraszam, wystąpił chwilowy błąd po mojej stronie. Spróbuj ponownie za chwilę."

    def __init__(self, inner: Responder, *, fallback: str = _FALLBACK) -> None:
        self._inner = inner
        self._fallback = fallback

    async def respond(self, message: InboundMessage) -> str:
        try:
            return await self._inner.respond(message)
        except WorkMateError as exc:
            logger.warning(
                "Błąd obsługi wiadomości (nadawca=%r, rozmowa=%r): %s",
                message.sender,
                message.conversation_id,
                exc,
            )
            return self._fallback
        except Exception:
            logger.exception(
                "Nieoczekiwany błąd obsługi wiadomości (nadawca=%r, rozmowa=%r)",
                message.sender,
                message.conversation_id,
            )
            return self._fallback
