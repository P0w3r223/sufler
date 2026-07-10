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

from typing import TYPE_CHECKING, Any

from workmate.core.ports.llm import AssistantTurn, RawTurn, ToolResults, UserText

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta

    from workmate.core.domain.conversation import (
        Conversation,
        ConversationMessage,
        ConversationSearchHit,
    )
    from workmate.core.ports.conversations import ConversationStore
    from workmate.core.ports.llm import TranscriptEntry

# Przybliżenie: średnio ~4 znaki na token. Zaniża/zawyża, ale jest tanie i stałe.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Przybliż liczbę tokenów tekstu (deterministycznie, bez wołania API)."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


class ConversationService:
    """Pamięć rozmów: utrzymuje aktywny wątek per (kanał, rozmowa) z limitem kontekstu."""

    def __init__(
        self,
        store: ConversationStore,
        *,
        max_context_tokens: int,
        idle_timeout: timedelta | None = None,
    ) -> None:
        if max_context_tokens < 1:
            raise ValueError("max_context_tokens musi być >= 1")
        self._store = store
        self._max = max_context_tokens
        # None → kryterium bezczynności wyłączone (zachowanie sprzed ADR 0012).
        self._idle_timeout = idle_timeout

    def prepare_turn(
        self,
        channel: str,
        external_id: str,
        user_text: str,
        *,
        now: datetime | None = None,
    ) -> tuple[str, list[ConversationMessage], bool]:
        """Przygotuj turę: (id rozmowy, historia SPRZED tej wiadomości, czy rollover).

        Rollover (patrz ``_should_roll_over``): gdy dołożenie tury ma zacząć nowy wątek —
        z powodu limitu kontekstu albo bezczynności — domyka bieżącą rozmowę i otwiera
        nową (świeży kontekst). Wiadomości NIE utrwala — robi to ``record_run`` po
        uzyskaniu odpowiedzi, więc błąd runtime nie zostawia osieroconej tury.

        ``now`` (znacznik chwili, zwykle podany przez adapter — rdzeń nie woła zegara)
        włącza kryterium bezczynności; ``None`` je pomija. Uwaga (miękka bramka):
        pierwsza wiadomość dłuższa niż limit i tak otwiera rozmowę (gałąź
        ``active is None`` nie sprawdza limitu) — rollover nastąpi dopiero przy kolejnej
        turze. To akceptowalne dla przybliżonego limitu. Od ADR 0011 do sumy tokenów
        rozmowy wliczają się też WYNIKI narzędzi (pełne tury zapisuje ``record_run``),
        więc duży wynik (np. ``get_note``) wcześniej wywoła rollover na KOLEJNEJ turze.
        """
        estimate = estimate_tokens(user_text)
        active = self._store.active_conversation(channel, external_id)
        rolled_over = False

        if active is None:
            active = self._store.open_conversation(channel, external_id)
        elif self._should_roll_over(active, estimate, now):
            self._store.close_conversation(active.id)
            active = self._store.open_conversation(channel, external_id)
            rolled_over = True

        prior = self._store.messages(active.id)
        return active.id, prior, rolled_over

    def _should_roll_over(
        self, active: Conversation, estimate: int, now: datetime | None
    ) -> bool:
        """Czy dołożenie tury ma domknąć bieżącą rozmowę i zacząć nowy wątek (rollover).

        Dwa NIEZALEŻNE kryteria:

        - **limit kontekstu** (ADR 0010): suma tokenów rozmowy + szacunek nowej tury
          przekroczyłaby ``max_context_tokens`` — ogranicza (i potania) każdy kontekst.
        - **bezczynność** (ADR 0012): od ostatniej aktywności (``updated_at``) minęło
          więcej niż ``idle_timeout`` — dzięki temu osobne w czasie rozmowy stają się
          osobnymi wątkami, a historia jednego rozmówcy nie zlewa się w jedną nić.

        Bezczynność liczymy tylko dla rozmowy Z TURAMI (``token_estimate > 0``): świeżo
        otwartego, pustego wątku nie ma po co rollować (brak historii do odcięcia) —
        uniknięcie osieroconej, pustej rozmowy. Wyłączona, gdy ``idle_timeout`` albo
        ``now`` to ``None`` (zachowanie sprzed ADR 0012).
        """
        if active.token_estimate + estimate > self._max:
            return True
        return (
            self._idle_timeout is not None
            and now is not None
            and active.token_estimate > 0
            and now - active.updated_at > self._idle_timeout
        )

    def start_new_thread(self, channel: str, external_id: str) -> bool:
        """Zamknij aktywny wątek NA ŻĄDANIE (komenda użytkownika) — jawna granica wątku.

        Trzecia (obok limitu kontekstu i bezczynności — ``_should_roll_over``) droga do
        nowego wątku: użytkownik sam kończy bieżącą rozmowę. Metoda tylko DOMYKA aktywny
        wątek — nowy otworzy się przy następnej wiadomości (gałąź ``active is None`` w
        ``prepare_turn``), spójnie z resztą logiki i bez tworzenia pustego wątku, gdyby
        użytkownik nic już nie napisał.

        Pustego, świeżo otwartego wątku (``token_estimate == 0``) nie zamyka — nie ma
        historii do odcięcia (jak przy bezczynności). Zwraca, czy faktycznie coś domknięto.
        """
        active = self._store.active_conversation(channel, external_id)
        if active is None or active.token_estimate == 0:
            return False
        self._store.close_conversation(active.id)
        return True

    def record_turn(self, conversation_id: str, user_text: str, reply_text: str) -> None:
        """Utrwal parę (wiadomość użytkownika, odpowiedź) — ścieżka TEXT-ONLY (ADR 0010).

        Rozdzielenie od ``prepare_turn`` sprawia, że gdy runtime rzuci błąd, w bazie
        nie zostaje tura użytkownika bez odpowiedzi (licząca się do limitu kontekstu).
        Zastąpiona przez ``record_run`` w bezstratnej ścieżce z pełnym transkryptem
        (ADR 0011); zostaje dla prostych, bezstanowych wywołań.
        """
        self._store.append_message(
            conversation_id, "user", user_text, estimate_tokens(user_text)
        )
        self._store.append_message(
            conversation_id, "assistant", reply_text, estimate_tokens(reply_text)
        )

    def record_run(
        self,
        conversation_id: str,
        entries: Sequence[TranscriptEntry],
        *,
        stop_reason: str = "",
    ) -> None:
        """Utrwal PEŁNĄ, bezstratną sekwencję tury (ADR 0011) — wołane PO odpowiedzi.

        ``entries`` to nowe, REPLAYOWALNE wpisy z ``AgentResult`` (wiadomość
        użytkownika + tury assistant/tool). Bloki zapisujemy VERBATIM; ``token_estimate``
        liczymy nad płaskim tekstem (thinking wykluczony — patrz ADR 0011). ``stop_reason``
        (jeśli podany) trafia na OSTATNIĄ turę asystenta jako informacja.
        """
        last_assistant = _last_index(entries, AssistantTurn)
        for index, entry in enumerate(entries):
            role, text, blocks, token_source = _row_of(entry)
            entry_stop = stop_reason if (index == last_assistant and stop_reason) else None
            self._store.append_message(
                conversation_id,
                role,
                text,
                estimate_tokens(token_source),
                blocks=blocks,
                stop_reason=entry_stop,
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

    def list_conversations(
        self, *, channel: str | None = None, limit: int = 50
    ) -> list[Conversation]:
        """Wylistuj rozmowy do podglądu historii (delegacja do magazynu)."""
        return self._store.list_conversations(channel=channel, limit=limit)

    def messages(self, conversation_id: str) -> list[ConversationMessage]:
        """Zwróć tury rozmowy w kolejności chronologicznej (delegacja do magazynu)."""
        return self._store.messages(conversation_id)


def _last_index(entries: Sequence[TranscriptEntry], cls: type) -> int:
    """Indeks OSTATNIEGO wpisu danego typu (albo -1)."""
    return max((i for i, e in enumerate(entries) if isinstance(e, cls)), default=-1)


def _row_of(entry: TranscriptEntry) -> tuple[str, str, list[dict[str, Any]] | None, str]:
    """Zmapuj wpis transkryptu na wiersz magazynu: (rola, tekst, bloki, źródło tokenów).

    „Tekst" to płaska projekcja do FTS/podglądu (pusta dla tur narzędziowych — poza
    indeksem). „Bloki" trzymane VERBATIM: dla asystenta bloki dostawcy (z ``signature``),
    dla narzędzia forma domenowa. „Źródło tokenów" to treść wliczana do limitu kontekstu
    (thinking celowo pominięty — patrz ADR 0011).
    """
    if isinstance(entry, UserText):
        return "user", entry.text, None, entry.text
    if isinstance(entry, AssistantTurn):
        blocks = [dict(b) for b in entry.blocks] or None
        return "assistant", entry.text, blocks, entry.text
    if isinstance(entry, ToolResults):
        blocks = [
            {"call_id": o.call_id, "content": o.content, "is_error": o.is_error}
            for o in entry.outputs
        ]
        return "tool", "", blocks, "".join(o.content for o in entry.outputs)
    # RawTurn: odtworzona tura z pamięci — nie powinna trafić do zapisu nowej tury,
    # ale gdyby, zachowujemy jej bloki bezstratnie (pusta projekcja tekstu).
    if isinstance(entry, RawTurn):
        return entry.role, "", [dict(b) for b in entry.blocks] or None, ""
    raise TypeError(f"Nieobsługiwany wpis transkryptu: {type(entry).__name__}")
