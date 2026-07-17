"""Przypadek użycia M3: „nowa notatka ze spotkania" (Faza 2 / ADR 0009).

Orkiestracja bez I/O: pobierz transkrypt (port) → streść (port) → złóż ``NoteMetadata``
w ZAMROŻONYM schemacie (Bramka 1) → zapisz przez bramkowany, DOPISUJĄCY
``NotesWriteService`` (Bramka 2, ADR 0006). Żadnego nowego narzędzia mutującego ani
nadpisywania — realizacja „pierwotnego marzenia" na istniejącej powierzchni zapisu.

Bezpieczeństwo: o miejscu zapisu (``project``, ``date``) decyduje WYWOŁUJĄCY, nie
streszczenie transkryptu (treść niezaufana). Dzięki temu transkrypt nie może
przekierować notatki do cudzego projektu przez wstrzyknięte pola.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from workmate.core.domain.notes import build_note_metadata

if TYPE_CHECKING:
    from datetime import date

    from workmate.core.application.services import NotesWriteService
    from workmate.core.domain.models import Note
    from workmate.core.ports.meeting import MeetingSummarizer, TranscriptSource


class MeetingNoteService:
    """Złóż notatkę ze spotkania: transkrypt → streszczenie → zapis (gated, append-only)."""

    def __init__(
        self,
        transcripts: TranscriptSource,
        summarizer: MeetingSummarizer,
        write_service: NotesWriteService,
    ) -> None:
        self._transcripts = transcripts
        self._summarizer = summarizer
        self._write_service = write_service

    def note_from_meeting(self, meeting_ref: str, *, project: str, meeting_date: date) -> Note:
        """Pobierz transkrypt, streść i zapisz notatkę; zwróć zapisaną notatkę.

        ``project`` i ``meeting_date`` pochodzą z kontekstu wywołania (drzwi/rejestr),
        nie z treści transkryptu — to one wyznaczają miejsce zapisu.
        """
        transcript = self._transcripts.fetch(meeting_ref)
        summary = self._summarizer.summarize(transcript)
        metadata = build_note_metadata(
            title=summary.title,
            project=project,
            date=meeting_date,
            participants=summary.participants,
            decisions=summary.decisions,
            action_items=summary.action_items,
            open_questions=summary.open_questions,
            tags=summary.tags,
        )
        return self._write_service.save_note(metadata, summary.body)
