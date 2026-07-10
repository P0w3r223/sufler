"""Port magazynu rozmów (Faza 2 / ADR 0010) — kontrakt trwałości historii czatu.

``Protocol`` (jak pozostałe porty) — dowolna implementacja o zgodnych sygnaturach
jest akceptowana bez dziedziczenia. Domyślny adapter: SQLite + FTS5 (wydajne
wyszukiwanie wielu starych rozmów); w testach — atrapa w pamięci. Znaczniki czasu
i identyfikatory nadaje implementacja, nie rdzeń.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from workmate.core.domain.conversation import (
        Conversation,
        ConversationMessage,
        ConversationSearchHit,
        ConversationSummary,
    )
    from workmate.core.domain.pricing import TokenUsage


class ConversationStore(Protocol):
    """Trwałość rozmów: aktywny wątek, dokładanie tur, historia i wyszukiwanie."""

    def active_conversation(self, channel: str, external_id: str) -> Conversation | None:
        """Zwróć aktywną rozmowę dla (kanał, rozmowa) albo ``None``."""
        ...

    def open_conversation(self, channel: str, external_id: str) -> Conversation:
        """Utwórz nową aktywną rozmowę i zwróć ją (pusta — bez tur, ``usage`` 0)."""
        ...

    def close_conversation(self, conversation_id: str) -> None:
        """Oznacz rozmowę jako domkniętą (``closed``) — pozostaje wyszukiwalna."""
        ...

    def append_message(
        self,
        conversation_id: str,
        role: str,
        text: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
        stop_reason: str | None = None,
        usage: TokenUsage | None = None,
    ) -> ConversationMessage:
        """Dołóż turę do rozmowy i zwróć ją (z nadanym id i znacznikiem czasu).

        ``blocks`` (ADR 0011) to VERBATIM sekwencja bloków treści tury zapisywana bez
        zmian (dla asystenta bloki dostawcy z ``signature``); ``None`` → wiersz text-only.
        ``text`` jest indeksowane w FTS; puste (tury narzędziowe) poza indeksem.
        ``usage`` (Design 2) to REALNE użycie tokenów tury asystenta (z pola ``usage``
        odpowiedzi API); ``None`` dla user/tool/legacy — do rozliczenia i bramki rolloveru.
        """
        ...

    def messages(self, conversation_id: str) -> list[ConversationMessage]:
        """Zwróć tury rozmowy w kolejności chronologicznej (także zarchiwizowane)."""
        ...

    # --- Kompaktowanie (ADR 0014) -------------------------------------------------

    def get(self, conversation_id: str) -> Conversation | None:
        """Zwróć rozmowę po id (niezależnie od statusu) albo ``None``.

        Kompaktowanie potrzebuje świeżego ``last_input_tokens`` po dopisaniu tury,
        gdy referencja do aktywnej rozmowy jest już nieaktualna.
        """
        ...

    def replay_messages(self, conversation_id: str) -> list[ConversationMessage]:
        """Zwróć tury do REPLAYU do API — tylko NIEzarchiwizowane, chronologicznie.

        Inaczej niż ``messages`` (pełna historia do podglądu), pomija tury zastąpione
        podsumowaniem (``archived``). Serwis dokleja przed nimi aktywne podsumowanie.
        """
        ...

    def archive_through(self, conversation_id: str, message_id: int) -> None:
        """Oznacz jako zarchiwizowane wszystkie tury rozmowy o ``id`` <= ``message_id``.

        Nie usuwa wierszy (historia i wyszukiwanie pozostają nienaruszone) — jedynie
        wypycha je z replayu do API. Idempotentne.
        """
        ...

    def save_summary(
        self,
        conversation_id: str,
        summary: str,
        covers_through_message_id: int,
        *,
        usage: TokenUsage | None = None,
    ) -> ConversationSummary:
        """Zapisz nowe podsumowanie wątku i zwróć je (z nadanym id i znacznikiem czasu).

        Poprzednie aktywne podsumowanie wątku oznacza jako zastąpione (``superseded``),
        tak by aktywne pozostało zawsze co najwyżej JEDNO. ``usage`` to koszt wywołania
        modelu podsumowującego (Design 2). ``covers_through_message_id`` obejmuje
        wszystkie tury o ``id`` <= wartości.
        """
        ...

    def active_summary(self, conversation_id: str) -> ConversationSummary | None:
        """Zwróć aktywne podsumowanie wątku albo ``None`` (jeszcze nie kompaktowano)."""
        ...

    def list_conversations(
        self, *, channel: str | None = None, limit: int = 50
    ) -> list[Conversation]:
        """Zwróć rozmowy (najnowsze pierwsze) do podglądu historii, opcjonalnie po kanale.

        Odczyt niezależny od aktywnego wątku i od treści (inaczej niż ``search``):
        listuje CAŁE archiwum — aktywne i domknięte — z sumą tokenów per rozmowa.
        ``limit`` chroni podgląd przed nieograniczonym wypisem długiej historii.
        """
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
