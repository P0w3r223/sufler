"""Porty przepływu „nowa notatka ze spotkania" (Faza 2, M3 / ADR 0009).

Dwa wąskie porty rozdzielają I/O od logiki: ``TranscriptSource`` pobiera surowy
transkrypt (realnie z Microsoft Graph — adapter odłożony do dostępu Azure), a
``MeetingSummarizer`` streszcza transkrypt do ``MeetingSummary`` (pola z transkryptu).
Rdzeń zależy tylko od tych kontraktów — implementacje żyją w adapterach, więc reguła
zależności rdzeń↛adaptery zostaje zachowana.

Treść transkryptu to DANE, nie polecenia (zasada przekrojowa roadmapy) — implementacja
``MeetingSummarizer`` nie wykonuje instrukcji znalezionych w transkrypcie.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from workmate.core.domain.models import MeetingSummary


class TranscriptSource(Protocol):
    """Źródło transkryptu spotkania (realnie: Microsoft Graph)."""

    def fetch(self, meeting_ref: str) -> str:
        """Zwróć surowy transkrypt spotkania wskazanego przez ``meeting_ref``."""
        ...


class MeetingSummarizer(Protocol):
    """Streszcza transkrypt do ``MeetingSummary`` (tytuł, uczestnicy, decyzje, …)."""

    def summarize(self, transcript: str) -> MeetingSummary:
        """Zwróć streszczenie transkryptu. ``project``/``date`` dokłada przepływ."""
        ...
