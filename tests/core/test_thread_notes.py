"""Testy przepływu F2 „zapisz to" — notatka z WĄTKU Teams (``ThreadNoteService``, ADR 0048).

Lustro ``test_meeting_notes`` dla innego źródła: zamiast transkryptu spotkania materiałem jest
treść WĄTKU (root + odpowiedzi) z portu ``ThreadSource``. Orkiestracja bez I/O: pobór wątku →
streszczenie (ten sam ``MeetingSummarizer``) → zapis przez bramkowany, create-only
``NotesWriteService`` (Bramka 2). W pełni na atrapach w pamięci (źródło wątku + summarizer +
``FakeNotesWriter``/``FakeProjectsRepository``), zgodnie z regułą rdzeń↛adaptery. Klucz F2:
uczestnicy z REALNYCH nadawców Graph (nie z LLM), prowieniencja (tag ``src:teams-thread`` + linia
``Źródło:``), idempotencja bez kosztu Claude, wyścig create-only.
"""

from __future__ import annotations

from datetime import date

import pytest

from tests.conftest import FakeNotesWriter, FakeProjectsRepository
from workmate.core.application.services import NotesWriteService
from workmate.core.application.thread_notes import ThreadNoteService
from workmate.core.domain.models import MeetingSummary, Note, Project
from workmate.core.domain.transcript import SpeakerRoster
from workmate.core.errors import NoteExistsError, WriteError
from workmate.core.ports.thread import ThreadContent

_EXTERNAL_ID = "team/chan/root"
_SOURCE_MSG = "msg-abc"


class _RecordingThreadSource:
    """Atrapa ``ThreadSource`` — zwraca stałą treść wątku i notuje żądania."""

    def __init__(self, content: ThreadContent) -> None:
        self._content = content
        self.requested: list[str] = []

    def fetch(self, external_id: str) -> ThreadContent:
        self.requested.append(external_id)
        return self._content


class _RecordingSummarizer:
    """Atrapa ``MeetingSummarizer`` — zwraca stałe streszczenie i notuje wejście (+ roster)."""

    def __init__(self, summary: MeetingSummary) -> None:
        self._summary = summary
        self.seen: list[str] = []
        self.rosters: list[SpeakerRoster] = []

    def summarize(self, text: str, roster: SpeakerRoster) -> MeetingSummary:
        self.seen.append(text)
        self.rosters.append(roster)
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
        # Model „halucynuje" nazwisko — musi zostać ZIGNOROWANE na rzecz realnych nadawców wątku.
        participants=["Wymyślony Ktoś"],
        decisions=["Zamrozic kontrakt v1"],
        action_items=["Anna: przygotowac draft"],
        open_questions=["Kto zatwierdza?"],
        tags=["api", "kontrakt"],
        body="  Krotkie streszczenie przebiegu.  ",
    )


def _content(
    participants: tuple[str, ...] = ("Anna Kowalska", "Jan Nowak"),
    text: str = "Anna Kowalska: ustalenia.\nJan Nowak: zgoda.",
) -> ThreadContent:
    return ThreadContent(text=text, participants=participants)


def _service(
    source: _RecordingThreadSource,
    summarizer: _RecordingSummarizer,
    writer: FakeNotesWriter | None = None,
) -> tuple[ThreadNoteService, FakeNotesWriter]:
    writer = writer or FakeNotesWriter()
    write_service = NotesWriteService(writer, _projects_repo())
    service = ThreadNoteService(source, summarizer, write_service)
    return service, writer


def test_note_from_thread_fetches_summarizes_and_saves():
    source = _RecordingThreadSource(_content())
    summarizer = _RecordingSummarizer(_summary())
    service, writer = _service(source, summarizer)

    outcome = service.note_from_thread(
        _EXTERNAL_ID, _SOURCE_MSG, project="scada-integration", on=date(2025, 6, 12)
    )

    # Wątek pobrany i przekazany do summarizera.
    assert source.requested == [_EXTERNAL_ID]
    assert summarizer.seen == ["Anna Kowalska: ustalenia.\nJan Nowak: zgoda."]
    assert outcome.created is True
    assert outcome.note is not None
    note = outcome.note
    # id DETERMINISTYCZNY z source_message_id (nie ze slug tytułu Claude), prefiks -thr- (ADR 0048).
    assert note.id.startswith("mpwik/scada-integration/2025-06-12-thr-")
    assert outcome.note_id == note.id
    assert writer.saved[note.id] is note
    assert note.metadata.decisions == ["Zamrozic kontrakt v1"]


def test_participants_come_from_real_senders_not_from_summary():
    # RDZEŃ anty-halucynacji (ADR 0047/0048): uczestnicy notatki pochodzą z REALNYCH nadawców
    # wątku (content.participants z metadanych Graph), a NIE z pola participants zwróconego przez
    # model (które tu celowo zawiera zmyślone nazwisko).
    source = _RecordingThreadSource(_content(participants=("Anna Kowalska", "Jan Nowak")))
    summarizer = _RecordingSummarizer(_summary())
    service, _ = _service(source, summarizer)

    outcome = service.note_from_thread(
        _EXTERNAL_ID, _SOURCE_MSG, project="scada-integration", on=date(2025, 6, 12)
    )

    assert outcome.note is not None
    assert outcome.note.metadata.participants == ["Anna Kowalska", "Jan Nowak"]
    assert "Wymyślony Ktoś" not in outcome.note.metadata.participants
    # Summarizer dostał roster zbudowany z realnych nadawców (allowlista nazwisk).
    assert summarizer.rosters[0].speakers == ("Anna Kowalska", "Jan Nowak")


def test_provenance_source_tag_and_body_marker_added():
    # Prowieniencja (ADR 0048 §5): źródło jedzie w tagu ``src:teams-thread`` oraz w linii
    # ``Źródło: wątek Teams (external_id)`` w treści — bez ruszania zamrożonego NoteMetadata.
    source = _RecordingThreadSource(_content())
    summarizer = _RecordingSummarizer(_summary())
    service, _ = _service(source, summarizer)

    outcome = service.note_from_thread(
        _EXTERNAL_ID, _SOURCE_MSG, project="scada-integration", on=date(2025, 6, 12)
    )

    assert outcome.note is not None
    assert "src:teams-thread" in outcome.note.metadata.tags
    assert f"Źródło: wątek Teams ({_EXTERNAL_ID})" in outcome.note.body


def test_write_location_comes_from_caller_not_from_thread():
    # Miejsce zapisu (projekt/data) wyznacza WYWOŁUJĄCY (wzmianka + Graph timestamp), nie treść
    # wątku (niezaufana) — wątek nie może przekierować notatki do cudzego projektu (ADR 0009 §3).
    source = _RecordingThreadSource(_content())
    summarizer = _RecordingSummarizer(_summary())
    service, _ = _service(source, summarizer)

    outcome = service.note_from_thread(
        _EXTERNAL_ID, _SOURCE_MSG, project="scada-integration", on=date(2024, 1, 2)
    )

    assert outcome.note is not None
    assert outcome.note.metadata.project == "scada-integration"
    assert outcome.note.metadata.date == date(2024, 1, 2)
    assert outcome.note.id.startswith("mpwik/scada-integration/2024-01-02-thr-")


def test_note_from_thread_is_idempotent_and_skips_fetch_and_summary():
    # Ponowienie TEJ SAMEJ wzmianki (ten sam source_message_id/data) NIE tworzy duplikatu -2:
    # druga próba jest idempotentna (created=False), a pobór wątku i Claude są POMINIĘTE (ADR 0048).
    source = _RecordingThreadSource(_content())
    summarizer = _RecordingSummarizer(_summary())
    service, writer = _service(source, summarizer)

    first = service.note_from_thread(
        _EXTERNAL_ID, _SOURCE_MSG, project="scada-integration", on=date(2025, 6, 12)
    )
    second = service.note_from_thread(
        _EXTERNAL_ID, _SOURCE_MSG, project="scada-integration", on=date(2025, 6, 12)
    )

    assert first.created is True
    assert second.created is False
    assert second.note is None
    assert second.note_id == first.note_id
    # Zapisano dokładnie JEDNĄ notatkę; drugi przebieg nie pobrał wątku ani nie streszczał.
    assert set(writer.saved) == {first.note_id}
    assert source.requested == [_EXTERNAL_ID]  # tylko pierwszy pobrał wątek
    assert summarizer.seen == ["Anna Kowalska: ustalenia.\nJan Nowak: zgoda."]  # tylko pierwszy


def test_unknown_project_raises_before_fetch_and_summary():
    # Tani strażnik (require_project): literówka w projekcie → WriteError PRZED poborem wątku i
    # Claude (w async ten koszt szedłby po cichu w tle). Nic nie pobrano ani nie streszczono.
    source = _RecordingThreadSource(_content())
    summarizer = _RecordingSummarizer(_summary())
    service, _ = _service(source, summarizer)

    with pytest.raises(WriteError):
        service.note_from_thread(
            _EXTERNAL_ID, _SOURCE_MSG, project="literowka", on=date(2025, 6, 12)
        )

    assert source.requested == []
    assert summarizer.seen == []


class _RacingWriter:
    """Writer symulujący WYŚCIG create-only: pre-check widzi pusto, ale zapis przegrywa kolizję.

    ``exists`` zwraca ``False`` do momentu próby zapisu (pre-check przechodzi → łańcuch rusza),
    a po niej ``True`` (równoległe zadanie zapisało notatkę tej wzmianki pierwsze). ``write``
    rzuca ``NoteExistsError`` — dokładnie ścieżka domykająca wyścig w ``note_from_thread``.
    """

    def __init__(self) -> None:
        self.saved: dict[str, Note] = {}
        self._collided = False

    def exists(self, note_id: str) -> bool:
        return self._collided

    def write(self, note: Note) -> None:
        self._collided = True
        raise NoteExistsError("równoległe zadanie zapisało notatkę tej wzmianki pierwsze")


def test_race_on_create_only_reports_idempotent_skip():
    # Wyścig (ADR 0048 §5): pre-check przeszedł (None), łańcuch policzył, ale create-only zapis
    # dostał kolizję → to NIE porażka, tylko idempotentne „już zapisana" (created=False, note=None)
    # z tym samym deterministycznym id. Pobór wątku i Claude JUŻ się wydarzyły (po pre-checku).
    source = _RecordingThreadSource(_content())
    summarizer = _RecordingSummarizer(_summary())
    service, _ = _service(source, summarizer, writer=_RacingWriter())  # type: ignore[arg-type]

    outcome = service.note_from_thread(
        _EXTERNAL_ID, _SOURCE_MSG, project="scada-integration", on=date(2025, 6, 12)
    )

    assert outcome.created is False
    assert outcome.note is None
    assert outcome.note_id.startswith("mpwik/scada-integration/2025-06-12-thr-")
    # Wyścig zachodzi PO pre-checku, więc wątek pobrano i streszczono (inaczej niż idempotencja).
    assert source.requested == [_EXTERNAL_ID]
    assert summarizer.seen == ["Anna Kowalska: ustalenia.\nJan Nowak: zgoda."]
