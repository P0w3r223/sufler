"""Modele domeny rozmów użytkownika z botem (Faza 2 / ADR 0010).

Osobno od ``models.py`` (zamrożony schemat notatek, Bramka 1) — rozmowy to dane
OPERACYJNE (historia czatu), nie baza wiedzy. Modele są czyste; znaczniki czasu
i identyfikatory nadaje magazyn (adapter), więc rdzeń nie woła zegara ani losowości
i pozostaje deterministyczny w testach.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


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
    token_estimate: int
    created_at: datetime
    blocks: list[dict[str, Any]] | None = None
    stop_reason: str | None = None


class Conversation(BaseModel):
    """Wątek rozmowy per (kanał, rozmowa zewnętrzna). Sekwencja tur do limitu kontekstu.

    ``token_estimate`` to suma przybliżeń tur — na jej podstawie serwis decyduje
    o rollover (nowa rozmowa po osiągnięciu limitu). ``status``: ``active`` albo
    ``closed`` (domknięta rollover-em, nadal wyszukiwalna).
    """

    id: str
    channel: str
    external_id: str
    status: str
    token_estimate: int
    created_at: datetime
    updated_at: datetime


class ConversationSearchHit(BaseModel):
    """Trafienie wyszukiwania w archiwum rozmów — tura + fragment wokół dopasowania."""

    conversation_id: str
    channel: str
    external_id: str
    role: str
    text: str
    snippet: str
    created_at: datetime
