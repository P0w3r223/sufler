"""Modele domeny wspólnego magazynu zdarzeń (EventStore, Faza 3 / ADR 0019).

Warstwa SPAJAJĄCA drzwi: zdarzenie z jednego drzwi (np. nowe issue z GitHuba) trafia do
wspólnej bazy lokalnej, skąd inne drzwi mogą je zobaczyć (notifier wypycha je do Teams,
narzędzie agenta pozwala je odczytać na dowolnych drzwiach). Jak rozmowy — to dane
OPERACYJNE, nie baza wiedzy; modele są czyste, a znaczniki czasu i identyfikatory nadaje
magazyn (adapter), więc rdzeń nie woła zegara ani losowości.

Treść zdarzenia (``title``/``summary``/``actor``) pochodzi ze źródła NIEZAUFANEGO (GitHub) —
to DANE, nie polecenia. Sanityzację przed zapisem robi ``EventService`` (obrona w głąb).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class NewEvent(BaseModel):
    """Zdarzenie zgłaszane do magazynu (jeszcze bez id ani znacznika przyjęcia).

    ``source`` to drzwi/warstwa pochodzenia (np. ``github``, ``teams``); ``kind`` to typ
    zdarzenia (np. ``issue_opened``, ``issue_comment``); ``external_id`` identyfikuje rzecz
    u źródła (np. numer issue) i razem z ``(source, kind)`` tworzy klucz DEDUPLIKACJI.
    ``actor`` to autor (login) — służy strażnikowi pętli self-ping. ``occurred_at`` to czas
    zdarzenia u źródła (nie czas przyjęcia).
    """

    source: str
    kind: str
    external_id: str
    actor: str = ""
    title: str = ""
    summary: str = ""
    url: str = ""
    occurred_at: datetime


class Event(NewEvent):
    """Zdarzenie przyjęte do magazynu — ``NewEvent`` wzbogacone o id i czas przyjęcia.

    ``id`` (monotoniczny, z bazy) jest KURSOREM notifiera (``read_since``) — gwarantuje
    kolejność i „co najmniej raz" bez gubienia wpisów między rundami.
    """

    id: int
    ingested_at: datetime = Field(...)
