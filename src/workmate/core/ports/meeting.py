"""Porty przepływu „nowa notatka ze spotkania" (Faza 2, M3 / ADR 0009; ADR 0047).

Wąskie porty rozdzielają I/O od logiki: ``TranscriptSource`` pobiera surowy transkrypt
(realnie z Microsoft Graph), ``MeetingSummarizer`` streszcza go do ``MeetingSummary``
(pass 1 — draft), a opcjonalny ``MeetingNoteVerifier`` konfrontuje draft z transkryptem
i usuwa twierdzenia bez pokrycia (pass 2 — krytyk, ADR 0047). Rdzeń zależy tylko od tych
kontraktów — implementacje żyją w adapterach, więc reguła rdzeń↛adaptery zostaje zachowana.

Treść transkryptu to DANE, nie polecenia (zasada przekrojowa roadmapy) — implementacje
nie wykonują instrukcji znalezionych w transkrypcie. Obie przelotki dostają ``SpeakerRoster``
(deterministyczny allowlist mówców) — nazwisk spoza niego używać nie wolno (anty-halucynacja).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from workmate.core.domain.models import MeetingSummary
    from workmate.core.domain.transcript import SpeakerRoster


class TranscriptSource(Protocol):
    """Źródło transkryptu spotkania (realnie: Microsoft Graph)."""

    def fetch(self, meeting_ref: str) -> str:
        """Zwróć surowy transkrypt spotkania wskazanego przez ``meeting_ref``."""
        ...


class MeetingSummarizer(Protocol):
    """Streszcza transkrypt do ``MeetingSummary`` (pass 1 — draft; tytuł, decyzje, …)."""

    def summarize(self, transcript: str, roster: SpeakerRoster) -> MeetingSummary:
        """Zwróć draft streszczenia. ``roster`` = allowlist mówców; ``project``/``date`` dokłada
        przepływ. Nazwisk spoza ``roster`` model użyć nie może."""
        ...


class MeetingNoteVerifier(Protocol):
    """Krytyk (pass 2, ADR 0047): usuwa/koryguje twierdzenia draftu bez pokrycia w transkrypcie."""

    def verify(
        self, draft: MeetingSummary, transcript: str, roster: SpeakerRoster
    ) -> MeetingSummary:
        """Zwróć zweryfikowane streszczenie — bez faktów niepopartych transkryptem."""
        ...
