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

from dataclasses import dataclass
from typing import Protocol


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
