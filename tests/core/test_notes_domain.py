"""Testy logiki domenowej notatek (``core/domain/notes.py``).

Czysta domena, bez I/O: filtr projektu (bez rozróżniania wielkości liter, bez mutacji
wejścia) i składanie ``NoteMetadata`` (normalizacja ``None`` → ``[]``). Test równoważności
zamyka refaktor „jedno miejsce konstrukcji metadanych".
"""

from __future__ import annotations

from datetime import date

from workmate.core.domain.models import Note, NoteMetadata
from workmate.core.domain.notes import build_note_metadata, notes_of_project


def _note(note_id: str, project: str) -> Note:
    return Note(
        id=note_id,
        metadata=NoteMetadata(title=note_id, project=project, date=date(2025, 1, 1)),
        body="",
    )


# --- notes_of_project -----------------------------------------------------------


def test_notes_of_project_returns_only_matching():
    notes = [_note("a", "mpwik"), _note("b", "workmate"), _note("c", "mpwik")]
    assert [n.id for n in notes_of_project(notes, "mpwik")] == ["a", "c"]


def test_notes_of_project_is_case_insensitive():
    notes = [_note("a", "MPWiK"), _note("b", "workmate")]
    result = notes_of_project(notes, "mpwik")
    assert [n.id for n in result] == ["a"]


def test_notes_of_project_empty_when_no_match():
    notes = [_note("a", "mpwik")]
    assert notes_of_project(notes, "nieznany") == []


def test_notes_of_project_does_not_mutate_input():
    notes = [_note("a", "mpwik"), _note("b", "workmate")]
    original = list(notes)
    notes_of_project(notes, "mpwik")
    assert notes == original  # lista wejściowa nietknięta


# --- build_note_metadata --------------------------------------------------------


def test_build_note_metadata_normalizes_none_lists_to_empty():
    meta = build_note_metadata(title="Notatka", project="workmate", date=date(2025, 6, 1))
    assert meta.participants == []
    assert meta.decisions == []
    assert meta.action_items == []
    assert meta.open_questions == []
    assert meta.tags == []


def test_build_note_metadata_keeps_passed_lists():
    meta = build_note_metadata(
        title="Notatka",
        project="workmate",
        date=date(2025, 6, 1),
        participants=["Anna"],
        tags=["api"],
    )
    assert meta.participants == ["Anna"]
    assert meta.tags == ["api"]


def test_build_note_metadata_equivalent_to_direct_construction():
    """Refaktor C: helper musi dać ten sam ``NoteMetadata`` co bezpośrednia konstrukcja."""
    kwargs = dict(
        title="Przegląd",
        project="scada",
        date=date(2025, 6, 12),
        participants=["Anna", "Marek"],
        decisions=["Zatwierdzono MVP"],
        action_items=["Szkic API"],
        open_questions=["Środowisko testowe?"],
        tags=["api", "kickoff"],
    )
    assert build_note_metadata(**kwargs) == NoteMetadata(**kwargs)
