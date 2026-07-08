"""Szew między drzwiami Teams a TREŚCIĄ odpowiedzi (Faza 2).

``Responder`` oddziela transport (Teams) od tego, co bot odpowiada. Dziś istnieje
tylko ``EchoResponder`` (spike M2: potwierdzenie odbioru). Później M1 (runtime
agenta) dostarczy ``RuntimeResponder`` implementujący ten sam protokół nad
rdzeniem WorkMate — bez zmiany handlera ani ``bot.py``/``app.py``. Moduł jest
wolny od importów SDK, więc szew i jego testy działają bez extra ``teams``.

Protokół żyje w warstwie DRZWI, nie w rdzeniu: reguła ``core ↛ adapters`` stoi.
Przyszły ``core/agent/runtime.py`` nie zaimportuje tego protokołu — to drzwi
opakują runtime rdzenia w ``Responder`` (strukturalnie, jak atrapy repo w testach).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from workmate.core.agent.runtime import AgentRuntime
    from workmate.core.application.services import NotesWriteService


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
    """Spike M2: potwierdza odbiór, nie dotykając rdzenia WorkMate."""

    async def respond(self, message: InboundMessage) -> str:
        return f"Odebrałem notatkę: {message.text}"


class RuntimeResponder:
    """Szew M1→Teams (ADR 0008): odpowiedź składa runtime agenta rdzenia.

    Wpięcie to jedna linia w ``app.py`` (``EchoResponder()`` → ``RuntimeResponder(runtime)``);
    handler i ``bot.py`` bez zmian. ``AgentRuntime.run`` jest synchroniczny (woła
    Claude API), więc uruchamiamy go w wątku puli, żeby nie blokować pętli aiohttp.
    Katalog runtime'u dla Teams budujemy BEZ ``write_service`` (Teams = mniej
    zaufane, ADR 0006) — agent przez Teams czyta, ale nie zapisuje.
    """

    def __init__(self, runtime: AgentRuntime) -> None:
        self._runtime = runtime

    async def respond(self, message: InboundMessage) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._runtime.run, message.text)


class SaveNoteResponder:
    """STUB (ADR 0008): responder zapisujący wiadomość jako notatkę przez ``save_note``.

    Świadomie NIEWPIĘTY: Teams jest mniej zaufane (ADR 0006), więc bezpośredni
    zapis z Teams wymaga osobnej decyzji (bramka zapisu per drzwi + parsowanie
    wiadomości w ``NoteMetadata``). Zostawiony jako punkt szwu — realizacja to
    kolejny krok M3/M4, nie spike.
    """

    def __init__(self, write_service: NotesWriteService) -> None:
        self._write_service = write_service

    async def respond(self, message: InboundMessage) -> str:
        raise NotImplementedError(
            "SaveNoteResponder to stub — zapis z Teams wymaga decyzji bramkowania (ADR 0006/0008)."
        )
