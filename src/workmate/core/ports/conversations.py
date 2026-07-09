"""Port magazynu rozmów (Faza 2 / ADR 0010) — kontrakt trwałości historii czatu.

``Protocol`` (jak pozostałe porty) — dowolna implementacja o zgodnych sygnaturach
jest akceptowana bez dziedziczenia. Domyślny adapter: SQLite + FTS5 (wydajne
wyszukiwanie wielu starych rozmów); w testach — atrapa w pamięci. Znaczniki czasu
i identyfikatory nadaje implementacja, nie rdzeń.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from workmate.core.domain.conversation import (
        Conversation,
        ConversationMessage,
        ConversationSearchHit,
    )


class ConversationStore(Protocol):
    """Trwałość rozmów: aktywny wątek, dokładanie tur, historia i wyszukiwanie."""

    def active_conversation(self, channel: str, external_id: str) -> Conversation | None:
        """Zwróć aktywną rozmowę dla (kanał, rozmowa) albo ``None``."""
        ...

    def open_conversation(self, channel: str, external_id: str) -> Conversation:
        """Utwórz nową aktywną rozmowę i zwróć ją (pusta, ``token_estimate=0``)."""
        ...

    def close_conversation(self, conversation_id: str) -> None:
        """Oznacz rozmowę jako domkniętą (``closed``) — pozostaje wyszukiwalna."""
        ...

    def append_message(
        self, conversation_id: str, role: str, text: str, token_estimate: int
    ) -> ConversationMessage:
        """Dołóż turę do rozmowy i zwróć ją (z nadanym id i znacznikiem czasu)."""
        ...

    def messages(self, conversation_id: str) -> list[ConversationMessage]:
        """Zwróć tury rozmowy w kolejności chronologicznej."""
        ...

    def search(
        self,
        query: str,
        *,
        channel: str | None = None,
        external_id: str | None = None,
        limit: int = 20,
    ) -> list[ConversationSearchHit]:
        """Przeszukaj archiwum rozmów po treści tur (opcjonalne filtry kanał/rozmowa)."""
        ...
