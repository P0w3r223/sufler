"""Szew między DRZWIAMI (Teams, Telegram, …) a TREŚCIĄ odpowiedzi (Faza 2).

``Responder`` oddziela transport (konkretne drzwi) od tego, co bot odpowiada —
wspólny dla wszystkich drzwi wejściowych, dlatego żyje tu, w `adapters/inbound/`,
a nie w pakiecie pojedynczych drzwi. Dziś dostępny ``EchoResponder`` (spike:
potwierdzenie odbioru); ``RuntimeResponder`` opakowuje runtime agenta rdzenia, a
``SaveNoteResponder`` (stub) — przyszły zapis. Przełączenie to jedna linia w
entry-poincie drzwi (``EchoResponder()`` → ``RuntimeResponder(runtime)``);
handler i wiring drzwi bez zmian.

Moduł jest wolny od importów SDK, więc szew i jego testy działają bez extra
drzwi. Reguła ``core ↛ adapters`` stoi: rdzeń nie zaimportuje tego protokołu —
to drzwi opakują runtime rdzenia w ``Responder`` (strukturalnie, jak atrapy repo
w testach).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from workmate.core.errors import WorkMateError
from workmate.core.ports.llm import AssistantTurn, UserText

if TYPE_CHECKING:
    from workmate.core.agent.runtime import AgentRuntime
    from workmate.core.application.conversations import ConversationService
    from workmate.core.application.services import NotesWriteService
    from workmate.core.domain.conversation import ConversationMessage
    from workmate.core.ports.llm import TranscriptEntry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InboundMessage:
    """Wiadomość z drzwi, znormalizowana do postaci niezależnej od SDK.

    ``text`` wystarcza echu; ``sender``/``conversation_id`` niosą atrybucję, której
    przyszłe ``save_note`` użyje bez zmiany sygnatury szwu (pola addytywne).
    """

    text: str
    sender: str = ""
    conversation_id: str = ""


class Responder(Protocol):
    """Kontrakt szwu: z wiadomości produkuje tekst odpowiedzi."""

    async def respond(self, message: InboundMessage) -> str: ...


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
    (Teams, Telegram) budujemy BEZ ``write_service`` (ADR 0006) — agent czyta, ale
    nie zapisuje.
    """

    def __init__(self, runtime: AgentRuntime) -> None:
        self._runtime = runtime

    async def respond(self, message: InboundMessage) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._runtime.run, message.text)


class SaveNoteResponder:
    """STUB (ADR 0008): responder zapisujący wiadomość jako notatkę przez ``save_note``.

    Świadomie NIEWPIĘTY: drzwi asynchroniczne (Teams, Telegram) są mniej zaufane
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


class ConversationalResponder:
    """Szew: runtime agenta z PAMIĘCIĄ rozmowy (wątkowość + limit kontekstu, ADR 0010).

    Utrzymuje historię per (kanał, rozmowa) w ``ConversationService``; przy limicie
    kontekstu automatycznie startuje nową rozmowę (rollover), a runtime dostaje
    historię BIEŻĄCEJ rozmowy jako kontekst. ``channel`` rozróżnia drzwi (``telegram``/
    ``teams``) w bazie rozmów. Wywołania synchroniczne (magazyn + runtime) idą w wątku
    puli, żeby nie blokować pętli async drzwi.
    """

    def __init__(
        self,
        runtime: AgentRuntime,
        conversations: ConversationService,
        *,
        channel: str,
    ) -> None:
        self._runtime = runtime
        self._conversations = conversations
        self._channel = channel
        # Serializuje SZYBKIE operacje na magazynie (wybór wątku, utrwalenie tury),
        # bo drzwi async wołają respond() z puli wątków (run_in_executor) i dwie tury
        # naraz mogłyby podwójnie otworzyć/osierocić rozmowę. Wolne wywołanie LLM
        # zostaje POZA zamkiem — równoległość między rozmowami zachowana.
        self._store_lock = threading.Lock()

    async def respond(self, message: InboundMessage) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._respond_sync, message)

    def _respond_sync(self, message: InboundMessage) -> str:
        # Klucz wątku: rozmowa z kanału (czat/wątek), a gdy jej brak — nadawca.
        external_id = message.conversation_id or message.sender or "default"
        with self._store_lock:
            conversation_id, history, rolled_over = self._conversations.prepare_turn(
                self._channel, external_id, message.text
            )
        # Błąd runtime propaguje się TU — nic nie utrwalono, brak osieroconej tury.
        reply = self._runtime.run(message.text, history=_to_transcript(history))
        with self._store_lock:
            self._conversations.record_turn(conversation_id, message.text, reply)
        if rolled_over:
            return (
                "(Poprzednia rozmowa osiągnęła limit kontekstu — zaczynam nową.)\n\n"
                f"{reply}"
            )
        return reply


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
    daje tę granicę drzwiom async (Teams, Telegram). Kontekst (nadawca, rozmowa) w logu.
    """

    _FALLBACK = (
        "Przepraszam, wystąpił chwilowy błąd po mojej stronie. "
        "Spróbuj ponownie za chwilę."
    )

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


def _to_transcript(messages: list[ConversationMessage]) -> list[TranscriptEntry]:
    """Zmapuj tury rozmowy na wpisy transkryptu LLM (pomija puste tury)."""
    entries: list[TranscriptEntry] = []
    for msg in messages:
        if not msg.text:
            continue
        if msg.role == "assistant":
            entries.append(AssistantTurn(msg.text, ()))
        else:
            entries.append(UserText(msg.text))
    return entries
