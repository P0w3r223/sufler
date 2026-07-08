"""Wspólne atrapy i dane dla testów.

Atrapy implementują porty (``NotesRepository`` / ``ProjectsRepository``)
strukturalnie — bez dziedziczenia — dzięki czemu serwisy testujemy w pełni
w pamięci, bez dotykania dysku.
"""
from __future__ import annotations

from datetime import date

import pytest

from workmate.core.domain.models import (
    Note,
    NoteMetadata,
    Project,
    ProjectStatusRecord,
)


def make_note(
    note_id: str,
    *,
    project: str,
    title: str,
    on: date,
    participants: list[str] | None = None,
    body: str = "",
    action_items: list[str] | None = None,
    tags: list[str] | None = None,
) -> Note:
    return Note(
        id=note_id,
        metadata=NoteMetadata(
            title=title,
            project=project,
            date=on,
            participants=participants or [],
            action_items=action_items or [],
            tags=tags or [],
        ),
        body=body,
    )


class FakeNotesRepository:
    """Atrapa ``NotesRepository`` trzymająca notatki w liście."""

    def __init__(self, notes: list[Note]) -> None:
        self._notes = notes

    def all(self) -> list[Note]:
        return list(self._notes)

    def get(self, note_id: str) -> Note | None:
        return next((n for n in self._notes if n.id == note_id), None)


class FakeProjectsRepository:
    """Atrapa ``ProjectsRepository`` trzymająca projekty i statusy w mapach."""

    def __init__(
        self,
        projects: list[Project],
        records: dict[str, ProjectStatusRecord],
    ) -> None:
        self._projects = projects
        self._records = records

    def all(self) -> list[Project]:
        return list(self._projects)

    def get(self, key: str) -> Project | None:
        return next((p for p in self._projects if p.key == key), None)

    def status_record(self, key: str) -> ProjectStatusRecord | None:
        return self._records.get(key)


class FakeNotesWriter:
    """Atrapa ``NotesWriter`` trzymająca zapisane notatki w mapie id → Note."""

    def __init__(self) -> None:
        self.saved: dict[str, Note] = {}

    def exists(self, note_id: str) -> bool:
        return note_id in self.saved

    def write(self, note: Note) -> None:
        self.saved[note.id] = note


@pytest.fixture
def sample_notes() -> list[Note]:
    return [
        make_note(
            "mpwik/scada-integration/2025-06-12-api",
            project="scada-integration",
            title="Przegląd kontraktu API",
            on=date(2025, 6, 12),
            participants=["Anna Kowalska", "Marek Nowak"],
            body="Domknęliśmy kontrakt API oparty o wąskie endpointy.",
            action_items=["Wdrożyć walidację wejścia", "Przygotować checklistę"],
            tags=["api", "bezpieczenstwo"],
        ),
        make_note(
            "mpwik/scada-integration/2025-05-14-kickoff",
            project="scada-integration",
            title="Kickoff integracji",
            on=date(2025, 5, 14),
            participants=["Anna Kowalska"],
            body="Zakres MVP to odczyt danych ze SCADA przez API.",
            action_items=["Szkic API"],
            tags=["kickoff", "api"],
        ),
        make_note(
            "biap/workmate/2025-06-10-schemat",
            project="workmate",
            title="Schemat notatki",
            on=date(2025, 6, 10),
            participants=["Piotr Zieliński"],
            body="Zablokowaliśmy schemat notatki i kontrakt narzędzi.",
            action_items=[],
            tags=["schemat"],
        ),
    ]
