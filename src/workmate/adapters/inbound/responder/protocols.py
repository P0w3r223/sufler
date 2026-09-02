"""Kontrakty warstwy odpowiadania: wiadomość przychodząca, ``Responder``, dostawa outboxu."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from workmate.core.domain.workspace import WorkspaceScope
from workmate.core.ports.llm import (
    Attachment,
)

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class InboundMessage:
    """Wiadomość z drzwi, znormalizowana do postaci niezależnej od SDK.

    ``text`` wystarcza echu; ``sender``/``conversation_id`` niosą atrybucję, której
    przyszłe ``save_note`` użyje bez zmiany sygnatury szwu (pola addytywne). ``attachments``
    (addytywne, domyślnie puste) niosą treść multimodalną z drzwi, które ją materializują.
    ``sender_id`` (AAD id nadawcy, addytywne) niesie CEL wyjściowej dostawy 1:1 (ADR 0027) —
    drzwi bez tego pojęcia zostawiają je puste, a narzędzie push-u się nie zbuduje.
    """

    text: str
    sender: str = ""
    conversation_id: str = ""
    attachments: tuple[Attachment, ...] = ()
    sender_id: str = ""
    # Szew „zapisz to" (ADR 0048), addytywne: ``mentions_bot`` = wiadomość @wzmiankuje bota
    # (warunek wyzwalacza); ``source_message_id`` = id wzmianki (klucz idempotencji, §5);
    # ``source_timestamp`` = Graph ``created`` wzmianki (deterministyczna data notatki). Drzwi bez
    # tego pojęcia zostawiają je puste/False, a router „zapisz to" nie zbuduje się (bramka OFF).
    mentions_bot: bool = False
    source_message_id: str = ""
    source_timestamp: str = ""
    # NAZWY z @wzmianek (``mentions[].mentionText``). Wzmianka jest adresatem, nie argumentem,
    # więc wyzwalacz „zapisz to" musi te słowa wykluczyć z szukania klucza projektu.
    mention_texts: tuple[str, ...] = ()


class Responder(Protocol):
    """Kontrakt szwu: z wiadomości produkuje tekst odpowiedzi."""

    async def respond(self, message: InboundMessage) -> str: ...


class OutboxDeliverer(Protocol):
    """Dwufazowa dostawa ze skrzynki nadawczej rozmowy (ADR 0009 paczki wdrożeniowej).

    ``snapshot`` musi paść PRZED turą, ``deliver`` po niej. Migawka jest granicą pochodzenia
    plików: rozmowy dzielą jeden wolumen brudnopisu (ADR 0010 paczki), więc bez niej nie da się
    odróżnić wyniku tej tury od pliku podłożonego wcześniej przez inną rozmowę.
    """

    def snapshot(self, scope: WorkspaceScope) -> None: ...

    def deliver(self, scope: WorkspaceScope) -> str: ...
