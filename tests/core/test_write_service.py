"""Testy przypadku użycia zapisu notatki (``NotesWriteService.save_note``).

Serwis wylicza miejsce zapisu (firma z rejestru + projekt + data + slug tytułu)
i zapisuje przez port ``NotesWriter``. Nigdy nie nadpisuje — przy kolizji dokłada
sufiks. Testowany w pełni w pamięci na atrapach (``FakeNotesWriter`` /
``FakeProjectsRepository``), zgodnie z regułą zależności rdzeń↛adaptery.
"""

from __future__ import annotations

from datetime import date

import pytest

from tests.conftest import FakeNotesWriter, FakeProjectsRepository
from workmate.core.application.services import NotesWriteService
from workmate.core.domain.models import NoteMetadata, Project
from workmate.core.errors import WriteError


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


def _metadata(*, project: str = "scada-integration", title: str = "Przeglad API") -> NoteMetadata:
    return NoteMetadata(
        title=title,
        project=project,
        date=date(2025, 6, 12),
        participants=["Anna Kowalska"],
    )


def test_save_note_resolves_company_from_registry_and_builds_id():
    writer = FakeNotesWriter()
    service = NotesWriteService(writer, _projects_repo())

    note = service.save_note(_metadata(), body="Treść notatki.")

    # Firma (mpwik) pochodzi z rejestru projektu, nie z metadanych notatki.
    assert note.id == "mpwik/scada-integration/2025-06-12-przeglad-api"
    assert writer.saved[note.id] is note


def test_save_note_strips_body_before_writing():
    writer = FakeNotesWriter()
    service = NotesWriteService(writer, _projects_repo())

    note = service.save_note(_metadata(), body="\n\n  Treść z białymi znakami  \n")

    assert note.body == "Treść z białymi znakami"


def test_save_note_unknown_project_raises_write_error():
    writer = FakeNotesWriter()
    service = NotesWriteService(writer, _projects_repo())

    with pytest.raises(WriteError):
        service.save_note(_metadata(project="nieznany"), body="Treść.")

    assert writer.saved == {}  # nic nie zapisano


def test_save_note_empty_slug_raises_write_error():
    writer = FakeNotesWriter()
    service = NotesWriteService(writer, _projects_repo())

    # Tytuł bez znaków ASCII [a-z0-9] daje pusty slug → błąd wejścia (nie ValueError).
    with pytest.raises(WriteError):
        service.save_note(_metadata(title="日本語"), body="Treść.")

    assert writer.saved == {}


def test_save_note_collision_appends_suffix_2():
    writer = FakeNotesWriter()
    service = NotesWriteService(writer, _projects_repo())

    first = service.save_note(_metadata(), body="Pierwsza.")
    second = service.save_note(_metadata(), body="Druga.")

    assert first.id == "mpwik/scada-integration/2025-06-12-przeglad-api"
    assert second.id == "mpwik/scada-integration/2025-06-12-przeglad-api-2"
    # Kolizja dokłada, a nie nadpisuje: obie notatki są zapisane.
    assert set(writer.saved) == {first.id, second.id}


def test_save_note_collision_picks_lowest_free_suffix():
    writer = FakeNotesWriter()
    service = NotesWriteService(writer, _projects_repo())

    ids = [service.save_note(_metadata(), body=f"n{i}").id for i in range(3)]

    assert ids == [
        "mpwik/scada-integration/2025-06-12-przeglad-api",
        "mpwik/scada-integration/2025-06-12-przeglad-api-2",
        "mpwik/scada-integration/2025-06-12-przeglad-api-3",
    ]
