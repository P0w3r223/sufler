"""Szew między DRZWIAMI (Teams, CLI, …) a TREŚCIĄ odpowiedzi (Faza 2).

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

Pakiet, nie moduł: kontrakty w ``protocols``, składanie transkryptu w ``transcript``,
respondery bez runtime'u w ``simple``, a pełna tura agenta w ``conversational``.
Ten plik jest JEDYNYM wejściem — ``from workmate.adapters.inbound.responder import <cokolwiek>``
działa jak przed rozbiciem.
"""

from __future__ import annotations

from workmate.adapters.inbound.responder.conversational import (
    _TAINTING_TOOLS,
    ConversationalResponder,
)
from workmate.adapters.inbound.responder.protocols import (
    InboundMessage,
    OutboxDeliverer,
    Responder,
)
from workmate.adapters.inbound.responder.simple import (
    EchoResponder,
    RuntimeResponder,
    SafeResponder,
    SaveNoteResponder,
)
from workmate.adapters.inbound.responder.transcript import (
    _to_transcript,
    _to_transcript_with_summary,
    _with_notices,
)

# Nazwy prywatne w re-eksporcie to nie przeoczenie: ``_TAINTING_TOOLS`` bierze stąd bramka
# skażenia rozmowy, a ``_to_transcript*`` i ``_with_notices`` — testy składania transkryptu.
# Rozbicie miało nie ruszyć ani jednego importu, więc nie rusza też tych.
__all__ = [
    "ConversationalResponder",
    "EchoResponder",
    "InboundMessage",
    "OutboxDeliverer",
    "Responder",
    "RuntimeResponder",
    "SafeResponder",
    "SaveNoteResponder",
    "_TAINTING_TOOLS",
    "_to_transcript",
    "_to_transcript_with_summary",
    "_with_notices",
]
