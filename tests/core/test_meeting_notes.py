"""Testy przepływu M3 „nowa notatka ze spotkania" (``MeetingNoteService``, ADR 0009).

Orkiestracja bez I/O: transkrypt (port) → streszczenie (port) → zapis przez bramkowany,
dopisujący ``NotesWriteService`` (Bramka 2). Testowane w pełni na atrapach w pamięci
(źródło transkryptu + summarizer + ``FakeNotesWriter``/``FakeProjectsRepository``),
zgodnie z regułą zależności rdzeń↛adaptery. Realny Graph i LLM są poza rdzeniem.
"""
from __future__ import annotations

from datetime import date

from tests.conftest import FakeNotesWriter, FakeProjectsRepository
from workmate.core.application.meeting_notes import MeetingNoteService
from workmate.core.application.services import NotesWriteService
from workmate.core.domain.models import MeetingSummary, Project


class _RecordingTranscripts:
    """Atrapa ``TranscriptSource`` — zwraca stały transkrypt i notuje żądania."""

    def __init__(self, transcript: str) -> None:
        self._transcript = transcript
        self.requested: list[str] = []

    def fetch(self, meeting_ref: str) -> str:
        self.requested.append(meeting_ref)
        return self._transcript


class _RecordingSummarizer:
    """Atrapa ``MeetingSummarizer`` — zwraca stałe streszczenie i notuje wejście."""

    def __init__(self, summary: MeetingSummary) -> None:
        self._summary = summary
        self.seen: list[str] = []

    def summarize(self, transcript: str) -> MeetingSummary:
        self.seen.append(transcript)
        return self._summary


def _projects_repo() -> FakeProjectsRepository:
    return FakeProjectsRepository(
        [
            Project(
                key="scada-integration",
                company="mpwik",
                name="Integracja SCADA MPWiK",
                description="Integracja MPWiK",
            )
        ],
        records={},
    )


def _summary() -> MeetingSummary:
    return MeetingSummary(
        title="Przeglad API",
        participants=["Anna Kowalska", "Jan Nowak"],
        decisions=["Zamrozic kontrakt v1"],
        action_items=["Anna: przygotowac draft"],
        open_questions=["Kto zatwierdza?"],
        tags=["api", "kontrakt"],
        body="  Krotkie streszczenie przebiegu.  ",
    )


def _service(
    transcripts: _RecordingTranscripts, summarizer: _RecordingSummarizer
) -> tuple[MeetingNoteService, FakeNotesWriter]:
    writer = FakeNotesWriter()
    write_service = NotesWriteService(writer, _projects_repo())
    return MeetingNoteService(transcripts, summarizer, write_service), writer


def test_note_from_meeting_summarizes_transcript_and_saves():
    transcripts = _RecordingTranscripts("Transkrypt: ... ustalenia ...")
    summarizer = _RecordingSummarizer(_summary())
    service, writer = _service(transcripts, summarizer)

    note = service.note_from_meeting(
        "meeting-42", project="scada-integration", meeting_date=date(2025, 6, 12)
    )

    # Transkrypt pobrany i przekazany do summarizera.
    assert transcripts.requested == ["meeting-42"]
    assert summarizer.seen == ["Transkrypt: ... ustalenia ..."]
    # Zapis przez bramkowany save_note: firma (mpwik) z rejestru, id z metadanych.
    assert note.id == "mpwik/scada-integration/2025-06-12-przeglad-api"
    assert writer.saved[note.id] is note
    # Pola z transkryptu trafiają do notatki; body przycięte przez save_note.
    assert note.metadata.decisions == ["Zamrozic kontrakt v1"]
    assert note.metadata.action_items == ["Anna: przygotowac draft"]
    assert note.body == "Krotkie streszczenie przebiegu."


def test_write_location_comes_from_caller_not_from_summary():
    # Streszczenie nie niesie project/date — miejsce zapisu wyznacza wywołujący,
    # nie treść niezaufana z transkryptu (ochrona przed wstrzykniętym przekierowaniem).
    transcripts = _RecordingTranscripts("dowolna tresc")
    summarizer = _RecordingSummarizer(_summary())
    service, _ = _service(transcripts, summarizer)

    note = service.note_from_meeting(
        "m1", project="scada-integration", meeting_date=date(2024, 1, 2)
    )

    assert note.metadata.project == "scada-integration"
    assert note.metadata.date == date(2024, 1, 2)
    assert note.id == "mpwik/scada-integration/2024-01-02-przeglad-api"


def test_note_from_meeting_is_append_only_on_collision():
    # Dwie notatki z tego samego spotkania/tytułu/daty → druga dostaje sufiks,
    # nic nie jest nadpisywane (reuse bramkowanej, dopisującej ścieżki zapisu).
    transcripts = _RecordingTranscripts("tresc")
    summarizer = _RecordingSummarizer(_summary())
    service, writer = _service(transcripts, summarizer)

    first = service.note_from_meeting(
        "m1", project="scada-integration", meeting_date=date(2025, 6, 12)
    )
    second = service.note_from_meeting(
        "m2", project="scada-integration", meeting_date=date(2025, 6, 12)
    )

    assert first.id == "mpwik/scada-integration/2025-06-12-przeglad-api"
    assert second.id == "mpwik/scada-integration/2025-06-12-przeglad-api-2"
    assert set(writer.saved) == {first.id, second.id}
