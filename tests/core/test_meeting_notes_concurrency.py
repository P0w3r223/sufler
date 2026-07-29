"""Współbieżność notatki ze spotkania (B3 / ADR 0043) — realny ``ThreadPoolExecutor`` i writer.

Pozostałe testy async używają schedulera inline (deterministyczne), więc NIE ćwiczą prawdziwej
równoległości. Ten test zamyka lukę: dwie nici składają notatkę TEGO SAMEGO spotkania naraz, z
realnym create-only ``MarkdownNotesWriter``. Bariera wymusza interleave (obie przechodzą pre-check,
zanim którakolwiek zapisze) — sprawdzamy, że powstaje DOKŁADNIE JEDNA notatka (bez korupcji przez
współdzielony plik tymczasowy), a wyniki są spójne (jeden ``created``, drugi idempotentnie pomija).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from tests.conftest import FakeProjectsRepository
from workmate.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
from workmate.core.application.meeting_notes import MeetingNoteService
from workmate.core.application.services import NotesWriteService
from workmate.core.domain.models import MeetingSummary, Project
from workmate.core.domain.transcript import SpeakerRoster


class _FixedTranscripts:
    def fetch(self, meeting_ref: str) -> str:
        return "Transkrypt spotkania."


class _BarrierSummarizer:
    """Streszcza dopiero, gdy OBIE nici dotrą do bariery — czyli po przejściu pre-checku obu."""

    def __init__(self, barrier: threading.Barrier) -> None:
        self._barrier = barrier

    def summarize(self, transcript: str, roster: SpeakerRoster) -> MeetingSummary:
        self._barrier.wait(timeout=5)
        return MeetingSummary(
            title="Przeglad",
            participants=["Anna"],
            decisions=["d"],
            action_items=["a"],
            open_questions=[],
            tags=["t"],
            body="Streszczenie.",
        )


def _projects() -> FakeProjectsRepository:
    return FakeProjectsRepository(
        [Project(key="scada-integration", company="mpwik", name="SCADA", description="d")],
        records={},
    )


def test_concurrent_same_meeting_writes_exactly_one_note(tmp_path):
    write_service = NotesWriteService(MarkdownNotesWriter(tmp_path), _projects())
    service = MeetingNoteService(
        _FixedTranscripts(), _BarrierSummarizer(threading.Barrier(2)), write_service
    )

    def run():
        return service.note_from_meeting(
            "join-abc", project="scada-integration", meeting_date=date(2025, 6, 12)
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [f.result() for f in (executor.submit(run), executor.submit(run))]

    # Dokładnie JEDNA notatka na dysku — brak korupcji przez współdzielony plik tymczasowy.
    files = list(tmp_path.rglob("*.md"))
    assert len(files) == 1
    # Oba wyniki wskazują ten sam deterministyczny id; jeden zapisał, drugi idempotentnie pominął.
    assert {o.note_id for o in outcomes} == {outcomes[0].note_id}
    assert sorted(o.created for o in outcomes) == [False, True]
