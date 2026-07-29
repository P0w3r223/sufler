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
from workmate.core.domain.transcript import SpeakerRoster


class _RecordingTranscripts:
    """Atrapa ``TranscriptSource`` — zwraca stały transkrypt i notuje żądania."""

    def __init__(self, transcript: str) -> None:
        self._transcript = transcript
        self.requested: list[str] = []

    def fetch(self, meeting_ref: str) -> str:
        self.requested.append(meeting_ref)
        return self._transcript


class _RecordingSummarizer:
    """Atrapa ``MeetingSummarizer`` — zwraca stałe streszczenie i notuje wejście (+ roster)."""

    def __init__(self, summary: MeetingSummary) -> None:
        self._summary = summary
        self.seen: list[str] = []
        self.rosters: list[SpeakerRoster] = []

    def summarize(self, transcript: str, roster: SpeakerRoster) -> MeetingSummary:
        self.seen.append(transcript)
        self.rosters.append(roster)
        return self._summary


class _RecordingVerifier:
    """Atrapa ``MeetingNoteVerifier`` — zwraca podstawione streszczenie i notuje wejście."""

    def __init__(self, verified: MeetingSummary) -> None:
        self._verified = verified
        self.seen_drafts: list[MeetingSummary] = []
        self.seen_transcripts: list[str] = []
        self.seen_rosters: list[SpeakerRoster] = []

    def verify(
        self, draft: MeetingSummary, transcript: str, roster: SpeakerRoster
    ) -> MeetingSummary:
        self.seen_drafts.append(draft)
        self.seen_transcripts.append(transcript)
        self.seen_rosters.append(roster)
        return self._verified


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
    transcripts: _RecordingTranscripts,
    summarizer: _RecordingSummarizer,
    verifier: _RecordingVerifier | None = None,
) -> tuple[MeetingNoteService, FakeNotesWriter]:
    writer = FakeNotesWriter()
    write_service = NotesWriteService(writer, _projects_repo())
    service = MeetingNoteService(transcripts, summarizer, write_service, verifier=verifier)
    return service, writer


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


def test_participants_come_from_roster_not_from_summary():
    # RDZEŃ anty-halucynacji (ADR 0047): uczestnicy notatki pochodzą z DETERMINISTYCZNEGO rostera
    # (etykiety mówców w transkrypcie), a NIE z pola participants zwróconego przez model.
    transcript = (
        "Anna Kowalska: pierwsze zdanie spotkania.\n"
        "Jan Nowak: drugie zdanie spotkania.\n"
        "Anna Kowalska: trzecie zdanie.\n"
    )
    transcripts = _RecordingTranscripts(transcript)
    # Model „halucynuje" nieistniejące nazwisko w participants — musi zostać ZIGNOROWANE.
    bogus = _summary().model_copy(update={"participants": ["Wymyślony Ktoś"]})
    summarizer = _RecordingSummarizer(bogus)
    service, _ = _service(transcripts, summarizer)

    outcome = service.note_from_meeting(
        "m1", project="scada-integration", meeting_date=date(2025, 6, 12)
    )

    assert outcome.note is not None
    assert outcome.note.metadata.participants == ["Anna Kowalska", "Jan Nowak"]
    assert "Wymyślony Ktoś" not in outcome.note.metadata.participants
    # Summarizer dostał roster (kotwiczenie), nie tylko surowy transkrypt.
    assert summarizer.rosters[0].speakers == ("Anna Kowalska", "Jan Nowak")


def test_verifier_pass_replaces_draft_when_provided():
    # Gdy wstrzyknięty jest weryfikator (pass 2, ADR 0047), to JEGO wynik trafia do notatki,
    # a nie surowy draft summarizera. Weryfikator dostaje draft + transkrypt.
    transcripts = _RecordingTranscripts("Anna Kowalska: cos.\nJan Nowak: cos innego.\n")
    draft = _summary()
    verified = _summary().model_copy(
        update={"decisions": ["Tylko poparte transkryptem"], "tags": ["zweryfikowane"]}
    )
    summarizer = _RecordingSummarizer(draft)
    verifier = _RecordingVerifier(verified)
    service, _ = _service(transcripts, summarizer, verifier)

    outcome = service.note_from_meeting(
        "m1", project="scada-integration", meeting_date=date(2025, 6, 12)
    )

    assert outcome.note is not None
    assert outcome.note.metadata.decisions == ["Tylko poparte transkryptem"]
    assert outcome.note.metadata.tags == ["zweryfikowane"]
    # Weryfikator dostał DRAFT summarizera i ten sam transkrypt.
    assert verifier.seen_drafts == [draft]
    assert verifier.seen_transcripts == ["Anna Kowalska: cos.\nJan Nowak: cos innego.\n"]


def test_participants_come_from_roster_even_with_verifier_enabled():
    # RDZEŃ anty-halucynacji przy WŁĄCZONYM passie 2 (ADR 0047): weryfikator też może zhalucynować
    # tożsamość w participants — a mimo to uczestnicy notatki MUSZĄ pochodzić z deterministycznego
    # rostera, nie z wyniku modelu. Bez tego testu regres „participants = wynik weryfikatora"
    # przeszedłby niezauważony (istniejący test verifiera nie sprawdza participants).
    transcript = (
        "Anna Kowalska: pierwsze zdanie spotkania.\n"
        "Jan Nowak: drugie zdanie spotkania.\n"
        "Anna Kowalska: trzecie zdanie.\n"
    )
    transcripts = _RecordingTranscripts(transcript)
    draft = _summary()
    # Pass 2 „halucynuje" nazwisko spoza transkryptu w participants — musi zostać ZIGNOROWANE.
    verified = _summary().model_copy(update={"participants": ["Zmyślony Krytyk"]})
    summarizer = _RecordingSummarizer(draft)
    verifier = _RecordingVerifier(verified)
    service, _ = _service(transcripts, summarizer, verifier)

    outcome = service.note_from_meeting(
        "m1", project="scada-integration", meeting_date=date(2025, 6, 12)
    )

    assert outcome.note is not None
    # Uczestnicy z DETERMINISTYCZNEGO rostera, nie z (zhalucynowanego) wyniku weryfikatora.
    assert outcome.note.metadata.participants == ["Anna Kowalska", "Jan Nowak"]
    assert "Zmyślony Krytyk" not in outcome.note.metadata.participants
    # Weryfikator dostał TEN SAM deterministyczny roster (allowlist) co summarizer — bez niego
    # pass 2 nie mógłby egzekwować listy dozwolonych nazwisk.
    assert verifier.seen_rosters[0].speakers == ("Anna Kowalska", "Jan Nowak")


def test_without_verifier_draft_is_used_directly():
    # Domyślnie (verifier=None) zachowanie jednoprzelotowe (ADR 0041) — draft trafia wprost.
    transcripts = _RecordingTranscripts("Anna Kowalska: raz.\nJan Nowak: dwa.\n")
    summarizer = _RecordingSummarizer(_summary())
    service, _ = _service(transcripts, summarizer, verifier=None)

    outcome = service.note_from_meeting(
        "m1", project="scada-integration", meeting_date=date(2025, 6, 12)
    )

    assert outcome.note is not None
    assert outcome.note.metadata.decisions == ["Zamrozic kontrakt v1"]
