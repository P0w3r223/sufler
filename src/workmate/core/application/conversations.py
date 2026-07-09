"""Serwis rozmów (Faza 2 / ADR 0010) — wątkowość, limit kontekstu, rollover.

Logika bez I/O: zależy tylko od portu ``ConversationStore``, więc testujemy ją na
atrapie w pamięci. Dwie odpowiedzialności:

1. **Limit kontekstu + rollover.** Kontekst jednej rozmowy jest ograniczony
   (``max_context_tokens``, modestny). Gdy dołożenie tury przekroczyłoby limit,
   bieżąca rozmowa zostaje domknięta, a tura startuje NOWĄ rozmowę — dzięki temu
   każde wywołanie modelu ma ograniczony (i tani) kontekst, a stare rozmowy zostają
   w archiwum do wyszukania.
2. **Historia jako kontekst.** ``prepare_turn`` zwraca tury POPRZEDZAJĄCE bieżącą
   wiadomość — drzwi podają je runtime'owi jako kontekst, po czym ``record_reply``
   dokłada odpowiedź.

Przybliżenie tokenów jest celowo deterministyczne (``len(text) // 4``), bez wołania
API — do bramkowania długości kontekstu wystarczy, a testy są powtarzalne.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workmate.core.domain.conversation import (
        ConversationMessage,
        ConversationSearchHit,
    )
    from workmate.core.ports.conversations import ConversationStore

# Przybliżenie: średnio ~4 znaki na token. Zaniża/zawyża, ale jest tanie i stałe.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Przybliż liczbę tokenów tekstu (deterministycznie, bez wołania API)."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


class ConversationService:
    """Pamięć rozmów: utrzymuje aktywny wątek per (kanał, rozmowa) z limitem kontekstu."""

    def __init__(self, store: ConversationStore, *, max_context_tokens: int) -> None:
        if max_context_tokens < 1:
            raise ValueError("max_context_tokens musi być >= 1")
        self._store = store
        self._max = max_context_tokens

    def prepare_turn(
        self, channel: str, external_id: str, user_text: str
    ) -> tuple[str, list[ConversationMessage], bool]:
        """Przygotuj turę: (id rozmowy, historia SPRZED tej wiadomości, czy rollover).

        Rollover: gdy aktywna rozmowa + nowa tura przekroczyłaby limit, domyka ją i
        otwiera nową (świeży kontekst). Wiadomości NIE utrwala — robi to ``record_turn``
        po uzyskaniu odpowiedzi, więc błąd runtime nie zostawia osieroconej tury.

        Uwaga (miękka bramka): pierwsza wiadomość dłuższa niż limit i tak otwiera
        rozmowę (gałąź ``active is None`` nie sprawdza limitu) — rollover nastąpi
        dopiero przy kolejnej turze. To akceptowalne dla przybliżonego limitu.
        """
        estimate = estimate_tokens(user_text)
        active = self._store.active_conversation(channel, external_id)
        rolled_over = False

        if active is None:
            active = self._store.open_conversation(channel, external_id)
        elif active.token_estimate + estimate > self._max:
            self._store.close_conversation(active.id)
            active = self._store.open_conversation(channel, external_id)
            rolled_over = True

        prior = self._store.messages(active.id)
        return active.id, prior, rolled_over

    def record_turn(self, conversation_id: str, user_text: str, reply_text: str) -> None:
        """Utrwal parę (wiadomość użytkownika, odpowiedź) — wołane PO odpowiedzi runtime'u.

        Rozdzielenie od ``prepare_turn`` sprawia, że gdy runtime rzuci błąd, w bazie
        nie zostaje tura użytkownika bez odpowiedzi (licząca się do limitu kontekstu).
        """
        self._store.append_message(
            conversation_id, "user", user_text, estimate_tokens(user_text)
        )
        self._store.append_message(
            conversation_id, "assistant", reply_text, estimate_tokens(reply_text)
        )

    def search(
        self,
        query: str,
        *,
        channel: str | None = None,
        external_id: str | None = None,
        limit: int = 20,
    ) -> list[ConversationSearchHit]:
        """Przeszukaj archiwum rozmów (delegacja do magazynu)."""
        return self._store.search(
            query, channel=channel, external_id=external_id, limit=limit
        )
