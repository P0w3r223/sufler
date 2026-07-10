"""Modele domeny rozmów użytkownika z botem (Faza 2 / ADR 0010).

Osobno od ``models.py`` (zamrożony schemat notatek, Bramka 1) — rozmowy to dane
OPERACYJNE (historia czatu), nie baza wiedzy. Modele są czyste; znaczniki czasu
i identyfikatory nadaje magazyn (adapter), więc rdzeń nie woła zegara ani losowości
i pozostaje deterministyczny w testach.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from workmate.core.domain.pricing import TokenUsage


class ConversationMessage(BaseModel):
    """Pojedyncza tura w rozmowie (ADR 0010, wzbogacona w ADR 0011).

    ``role`` ∈ ``user`` / ``assistant`` / ``tool``. ``text`` to płaska projekcja
    (do FTS/podglądu i liczenia tokenów). ``blocks`` to VERBATIM sekwencja bloków
    treści tury — dla ``assistant`` nieprzezroczyste bloki dostawcy (z ``signature``
    thinking), dla ``tool`` forma domenowa ``[{call_id, content, is_error}]``.
    ``None`` dla wierszy sprzed ADR 0011 (odczyt degraduje do text-only).
    Rdzeń bloków NIE interpretuje — tylko je przenosi (opaque passthrough, ADR 0011).
    """

    id: int
    conversation_id: str
    role: str
    text: str
    created_at: datetime
    blocks: list[dict[str, Any]] | None = None
    stop_reason: str | None = None
    # Realne użycie tokenów tej tury (Design 2) — z pola ``usage`` odpowiedzi API. Na
    # wierszu ``assistant``; ``None`` dla user/tool oraz wierszy legacy (brak usage).
    usage: TokenUsage | None = None
    # Czy tura jest ZARCHIWIZOWANA przez kompaktowanie (ADR 0014) — oryginał zostaje w
    # bazie, ale wypada z replayu do API (zastąpiona podsumowaniem).
    archived: bool = False


class Conversation(BaseModel):
    """Wątek rozmowy per (kanał, rozmowa zewnętrzna). Sekwencja tur do limitu kontekstu.

    ``usage`` to ZSUMOWANE realne użycie tokenów rozmowy (Design 2) — na jego podstawie
    liczymy realną liczbę tokenów i KOSZT w podglądzie. ``last_context_tokens`` to rozmiar
    kontekstu OSTATNIEJ tury (wejście + cache + wyjście) — na jego podstawie serwis decyduje
    o rollover na LIMICIE. ``message_count`` (liczba tur) to sygnał „niepusty" dla bramek
    bezczynności i komendy ``/nowa`` — niezależny od usage (wątek z ``record_turn`` też
    jest niepusty). ``status``: ``active`` albo ``closed`` (domknięta, nadal wyszukiwalna).
    """

    id: str
    channel: str
    external_id: str
    status: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    last_context_tokens: int = 0
    # Rozmiar WEJŚCIA ostatniej tury (input + cache, BEZ wyjścia) — trigger kompaktowania
    # (ADR 0014): gdy > próg, stare tury zastępujemy podsumowaniem.
    last_input_tokens: int = 0
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class ConversationSummary(BaseModel):
    """Podsumowanie zarchiwizowanej części wątku (kompaktowanie, ADR 0014).

    Osobny rekord powiązany z wątkiem: ``summary`` (tekst), ``covers_through_message_id``
    (obejmuje wszystkie wiadomości o ``id`` <= temu), ``usage`` (koszt wywołania modelu
    podsumowującego — Design 2). Aktywne jest zawsze co najwyżej JEDNO na wątek; kolejne
    kompaktowanie tworzy nowe (obejmujące poprzednie + nowe tury), a stare oznacza jako
    zastąpione (``superseded``) — nie jest wtedy zwracane.
    """

    id: int
    conversation_id: str
    summary: str
    covers_through_message_id: int
    created_at: datetime
    usage: TokenUsage | None = None


class ConversationSearchHit(BaseModel):
    """Trafienie wyszukiwania w archiwum rozmów — tura + fragment wokół dopasowania."""

    conversation_id: str
    channel: str
    external_id: str
    role: str
    text: str
    snippet: str
    created_at: datetime
