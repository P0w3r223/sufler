"""Przypadek użycia „zapisz to": notatka z WĄTKU Teams po @wzmiance bota (ADR 0048).

Lustro ``meeting_notes`` dla innego źródła: zamiast transkryptu spotkania (WebVTT) materiałem
jest treść wątku (root + odpowiedzi). Orkiestracja bez I/O: pobierz wątek (port ``ThreadSource``)
→ streść istniejącym ``MeetingSummarizer`` (ten sam kontrakt, ADR 0047) → złóż ``NoteMetadata`` w
ZAMROŻONYM schemacie (Bramka 1) → zapisz bramkowanym, DOPISUJĄCYM, create-only ``NotesWriteService``
(Bramka 2, ADR 0006). Żadnego nowego narzędzia mutującego ani nadpisywania.

Bezpieczeństwo: o miejscu zapisu (``project``, ``on``) decyduje WYWOŁUJĄCY (wzmianka + Graph
timestamp), nie treść wątku (ADR 0009 §3) — wątek nie może przekierować notatki do cudzego
projektu. ``participants`` pochodzą z REALNYCH nadawców wątku (deterministycznie z metadanych
Graph), nie z LLM (anty-halucynacja, ADR 0047). Provenance (ADR 0048 §5): NoteMetadata NIETKNIĘTY
(Gate 1) — źródło jedzie w deterministycznym id, linii ``Źródło:`` w treści oraz tagu
``src:teams-thread``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sufler.core.domain.notes import build_note_metadata
from sufler.core.domain.transcript import SpeakerRoster
from sufler.core.errors import NoteExistsError

if TYPE_CHECKING:
    from datetime import date

    from sufler.core.application.services import NotesWriteService
    from sufler.core.domain.models import Note
    from sufler.core.ports.meeting import MeetingNoteVerifier, MeetingSummarizer
    from sufler.core.ports.thread import ThreadSource

# Tag prowieniencji (ADR 0048 §5) — źródło notatki bez ruszania zamrożonego ``NoteMetadata``.
_SOURCE_TAG = "src:teams-thread"


@dataclass(frozen=True)
class ThreadNoteOutcome:
    """Wynik złożenia notatki z wątku (lustro ``MeetingNoteOutcome``, ADR 0048).

    ``created=True`` → notatkę zapisano w tym przebiegu (``note`` niesie pełną treść).
    ``created=False`` → notatka tej wzmianki JUŻ istniała (idempotencja): pobór wątku i Claude
    POMINIĘTO, ``note`` jest ``None`` (ta ścieżka nie ma readera bazy — mamy tylko id).
    """

    note_id: str
    created: bool
    note: Note | None


class ThreadNoteService:
    """Złóż notatkę z wątku: treść wątku → streszczenie → zapis (gated, idempotentny)."""

    def __init__(
        self,
        source: ThreadSource,
        summarizer: MeetingSummarizer,
        write_service: NotesWriteService,
        verifier: MeetingNoteVerifier | None = None,
    ) -> None:
        self._source = source
        self._summarizer = summarizer
        self._write_service = write_service
        # Pass 2 (ADR 0047), opcjonalny: None → jednoprzelotowo. Ten sam krytyk co spotkania.
        self._verifier = verifier

    def note_from_thread(
        self, external_id: str, source_message_id: str, *, project: str, on: date
    ) -> ThreadNoteOutcome:
        """Pobierz wątek, streść i zapisz notatkę; ZWRÓĆ wynik (utworzona / już była).

        ``project`` i ``on`` pochodzą z kontekstu wzmianki (jawny arg + Graph timestamp), nie z
        treści wątku — to one wyznaczają miejsce zapisu. Idempotencja (ADR 0048 §5): jeśli notatka
        tej wzmianki (deterministyczny id z ``source_message_id``) już istnieje, KRÓTKO zwracamy
        ``created=False`` bez poboru wątku i bez kosztu Claude. Wyścig domyka create-only zapis.
        """
        # Tani strażnik PRZED I/O: literówka w projekcie nie ma płacić za pobór wątku + Claude.
        self._write_service.require_project(project)
        existing_id = self._write_service.thread_note_id(
            source_message_id, project=project, date=on
        )
        if existing_id is not None:
            return ThreadNoteOutcome(note_id=existing_id, created=False, note=None)
        content = self._source.fetch(external_id)
        # Uczestnicy DETERMINISTYCZNIE z realnych nadawców Graph (nie z LLM). Roster jest
        # jednocześnie allowlistą nazwisk dla summarizera i jedynym źródłem pola participants.
        roster = SpeakerRoster(speakers=content.participants, diarized=bool(content.participants))
        summary = self._summarizer.summarize(content.text, roster)
        if self._verifier is not None:
            summary = self._verifier.verify(summary, content.text, roster)
        metadata = build_note_metadata(
            title=summary.title,
            project=project,
            date=on,
            # participants NIE z modelu (anty-halucynacja) — tylko z deterministycznego rostera.
            participants=roster.participants(),
            decisions=summary.decisions,
            action_items=summary.action_items,
            open_questions=summary.open_questions,
            # Prowieniencja w tagu (ADR 0048 §5) — ``NoteMetadata`` nietknięty (Gate 1).
            tags=[*summary.tags, _SOURCE_TAG],
        )
        body = _with_source_note(summary.body, external_id)
        try:
            note = self._write_service.save_thread_note(
                metadata, body, source_message_id=source_message_id
            )
        except NoteExistsError:
            # Wyścig (ADR 0048 §5): pre-check przeszedł, ale RÓWNOLEGŁE zadanie zapisało notatkę tej
            # wzmianki pierwsze (create-only). To NIE porażka — idempotentnie raportujemy „już
            # zapisana" tym samym deterministycznym id, spójnie z pre-checkiem.
            settled_id = self._write_service.thread_note_id(
                source_message_id, project=project, date=on
            )
            return ThreadNoteOutcome(note_id=settled_id or "", created=False, note=None)
        return ThreadNoteOutcome(note_id=note.id, created=True, note=note)


def _with_source_note(body: str, external_id: str) -> str:
    """Dopisz do treści JEDNĄ linię prowieniencji (ADR 0048 §5) — źródło bez zmiany schematu.

    ``external_id`` (``team/channel/root``) to identyfikator wątku, nie sekret; zapisujemy go
    wprost, by notatka niosła ślad źródła. Treść wątku to DANE — nie interpolujemy jej do linii.
    """
    marker = f"Źródło: wątek Teams ({external_id})"
    return f"{body}\n\n{marker}" if body else marker
