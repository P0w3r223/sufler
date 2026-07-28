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

    outcome = service.note_from_meeting(
        "meeting-42", project="scada-integration", meeting_date=date(2025, 6, 12)
    )

    # Transkrypt pobrany i przekazany do summarizera.
    assert transcripts.requested == ["meeting-42"]
    assert summarizer.seen == ["Transkrypt: ... ustalenia ..."]
    assert outcome.created is True
    assert outcome.note is not None
    note = outcome.note
    # id DETERMINISTYCZNY z meeting_ref (nie ze slug tytułu Claude), ADR 0043.
    assert note.id.startswith("mpwik/scada-integration/2025-06-12-mtg-")
    assert outcome.note_id == note.id
    assert writer.saved[note.id] is note
    # Pola z transkryptu trafiają do notatki; body przycięte przez save_meeting_note.
    assert note.metadata.decisions == ["Zamrozic kontrakt v1"]
    assert note.metadata.action_items == ["Anna: przygotowac draft"]
    assert note.body == "Krotkie streszczenie przebiegu."


def test_write_location_comes_from_caller_not_from_summary():
    # Streszczenie nie niesie project/date — miejsce zapisu wyznacza wywołujący,
    # nie treść niezaufana z transkryptu (ochrona przed wstrzykniętym przekierowaniem).
    transcripts = _RecordingTranscripts("dowolna tresc")
    summarizer = _RecordingSummarizer(_summary())
    service, _ = _service(transcripts, summarizer)

    outcome = service.note_from_meeting(
        "m1", project="scada-integration", meeting_date=date(2024, 1, 2)
    )

    assert outcome.note is not None
    assert outcome.note.metadata.project == "scada-integration"
    assert outcome.note.metadata.date == date(2024, 1, 2)
    assert outcome.note.id.startswith("mpwik/scada-integration/2024-01-02-mtg-")


def test_note_from_meeting_is_idempotent_on_same_meeting_ref():
    # Ponowienie TEGO SAMEGO spotkania (ten sam meeting_ref/data) NIE tworzy duplikatu -2:
    # druga próba jest idempotentna (created=False), a pobór transkryptu i Claude są POMINIĘTE
    # (ADR 0043). id deterministyczny z meeting_ref daje kolizję zamiast sufiksu.
    transcripts = _RecordingTranscripts("tresc")
    summarizer = _RecordingSummarizer(_summary())
    service, writer = _service(transcripts, summarizer)

    first = service.note_from_meeting(
        "m1", project="scada-integration", meeting_date=date(2025, 6, 12)
    )
    second = service.note_from_meeting(
        "m1", project="scada-integration", meeting_date=date(2025, 6, 12)
    )

    assert first.created is True
    assert second.created is False
    assert second.note is None
    assert second.note_id == first.note_id
    # Zapisano dokładnie JEDNĄ notatkę; drugi przebieg nie pobrał transkryptu ani nie streszczał.
    assert set(writer.saved) == {first.note_id}
    assert transcripts.requested == ["m1"]  # tylko pierwszy pobrał transkrypt
    assert summarizer.seen == ["tresc"]  # tylko pierwszy streścił


def test_unknown_project_skips_fetch_and_summary():
    # Tani strażnik (ADR 0043): literówka w projekcie → WriteError PRZED poborem transkryptu i
    # Claude (w async ten koszt szedłby po cichu w tle). Nic nie pobrano ani nie streszczono.
    import pytest

    from workmate.core.errors import WriteError

    transcripts = _RecordingTranscripts("tresc")
    summarizer = _RecordingSummarizer(_summary())
    service, _ = _service(transcripts, summarizer)

    with pytest.raises(WriteError):
        service.note_from_meeting("m1", project="literowka", meeting_date=date(2025, 6, 12))

    assert transcripts.requested == []
    assert summarizer.seen == []


def test_distinct_meeting_refs_same_date_are_separate_notes():
    # Różne spotkania tego samego dnia → różne id (hash z meeting_ref), dwie osobne notatki.
    transcripts = _RecordingTranscripts("tresc")
    summarizer = _RecordingSummarizer(_summary())
    service, writer = _service(transcripts, summarizer)

    a = service.note_from_meeting("m1", project="scada-integration", meeting_date=date(2025, 6, 12))
    b = service.note_from_meeting("m2", project="scada-integration", meeting_date=date(2025, 6, 12))

    assert a.note_id != b.note_id
    assert set(writer.saved) == {a.note_id, b.note_id}
