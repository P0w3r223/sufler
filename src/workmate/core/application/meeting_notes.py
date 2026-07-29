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

from dataclasses import dataclass
from typing import TYPE_CHECKING

from workmate.core.domain.notes import build_note_metadata
from workmate.core.domain.transcript import parse_speaker_roster
from workmate.core.errors import NoteExistsError

if TYPE_CHECKING:
    from datetime import date

    from workmate.core.application.services import NotesWriteService
    from workmate.core.domain.models import Note
    from workmate.core.ports.meeting import (
        MeetingNoteVerifier,
        MeetingSummarizer,
        TranscriptSource,
    )


@dataclass(frozen=True)
class MeetingNoteOutcome:
    """Wynik złożenia notatki ze spotkania (ADR 0043).

    ``created=True`` → notatkę zapisano w tym przebiegu (``note`` niesie pełną treść).
    ``created=False`` → notatka tego spotkania JUŻ istniała (idempotencja): pobór transkryptu i
    Claude POMINIĘTO, ``note`` jest ``None`` (ta ścieżka nie ma readera bazy — mamy tylko id).
    """

    note_id: str
    created: bool
    note: Note | None


class MeetingNoteService:
    """Złóż notatkę ze spotkania: transkrypt → streszczenie → zapis (gated, idempotentny)."""

    def __init__(
        self,
        transcripts: TranscriptSource,
        summarizer: MeetingSummarizer,
        write_service: NotesWriteService,
        verifier: MeetingNoteVerifier | None = None,
    ) -> None:
        self._transcripts = transcripts
        self._summarizer = summarizer
        self._write_service = write_service
        # Pass 2 (ADR 0047), opcjonalny jak authorizer/scheduler: None → jednoprzelotowo (0041).
        self._verifier = verifier

    def note_from_meeting(
        self, meeting_ref: str, *, project: str, meeting_date: date
    ) -> MeetingNoteOutcome:
        """Pobierz transkrypt, streść i zapisz notatkę; ZWRÓĆ wynik (utworzona / już była).

        ``project`` i ``meeting_date`` pochodzą z kontekstu wywołania (drzwi/rejestr), nie z treści
        transkryptu — to one wyznaczają miejsce zapisu. Idempotencja (ADR 0043): jeśli notatka tego
        spotkania (deterministyczny id z ``meeting_ref``) już istnieje, KRÓTKO zwracamy ``created=
        False`` bez poboru transkryptu i bez kosztu Claude. Wyścig domyka create-only zapis.
        """
        # Tani strażnik PRZED I/O: literówka w projekcie nie ma płacić za transkrypt + Claude.
        self._write_service.require_project(project)
        existing_id = self._write_service.meeting_note_id(
            meeting_ref, project=project, date=meeting_date
        )
        if existing_id is not None:
            return MeetingNoteOutcome(note_id=existing_id, created=False, note=None)
        transcript = self._transcripts.fetch(meeting_ref)
        # Kotwiczenie mówców (ADR 0047): DETERMINISTYCZNIE z transkryptu, nie z LLM. Roster jest
        # jednocześnie allowlistą nazwisk dla obu przelotek i jedynym źródłem pola participants.
        roster = parse_speaker_roster(transcript)
        summary = self._summarizer.summarize(transcript, roster)
        if self._verifier is not None:
            # Pass 2 (ADR 0047): krytyk usuwa twierdzenia bez pokrycia w transkrypcie.
            summary = self._verifier.verify(summary, transcript, roster)
        metadata = build_note_metadata(
            title=summary.title,
            project=project,
            date=meeting_date,
            # participants NIE z modelu (anty-halucynacja) — tylko z deterministycznego rostera.
            participants=roster.participants(),
            decisions=summary.decisions,
            action_items=summary.action_items,
            open_questions=summary.open_questions,
            tags=summary.tags,
        )
        try:
            note = self._write_service.save_meeting_note(
                metadata, summary.body, meeting_ref=meeting_ref
            )
        except NoteExistsError:
            # Wyścig (ADR 0043): pre-check przeszedł, ale RÓWNOLEGŁE zadanie zapisało notatkę tego
            # spotkania pierwsze (create-only). To NIE porażka — idempotentnie raportujemy „już
            # złożona" tym samym deterministycznym id, spójnie z pre-checkiem.
            settled_id = self._write_service.meeting_note_id(
                meeting_ref, project=project, date=meeting_date
            )
            return MeetingNoteOutcome(note_id=settled_id or "", created=False, note=None)
        return MeetingNoteOutcome(note_id=note.id, created=True, note=note)
