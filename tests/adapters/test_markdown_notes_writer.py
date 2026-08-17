"""Testy zapisu notatek do plików Markdown (``MarkdownNotesWriter``).

Weryfikuje odwrotność ``MarkdownNotesRepository``: roundtrip zapis→odczyt,
kontrolę kolizji (``exists``), atomowość (brak pliku ``.tmp`` po zapisie i brak
częściowego pliku przy awarii podmiany) oraz tworzenie brakujących katalogów
firmy/projektu. Adapter dotyka dysku, więc testy działają na ``tmp_path``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from workmate.adapters.outbound import markdown_notes_writer as writer_module
from workmate.adapters.outbound.markdown_notes_repo import MarkdownNotesRepository
from workmate.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
from workmate.core.domain.models import Note, NoteMetadata
from workmate.core.errors import NoteExistsError, WriteError


def _note(note_id: str, *, body: str = "Treść notatki.") -> Note:
    return Note(
        id=note_id,
        metadata=NoteMetadata(
            title="Przegląd integracji",
            project="scada-integration",
            date=date(2025, 6, 12),
            participants=["Anna Kowalska", "Marek Nowak"],
            action_items=["Wdrożyć walidację"],
            tags=["api"],
        ),
        body=body,
    )


def test_write_then_read_roundtrips_through_repository(tmp_path: Path):
    note = _note("mpwik/scada-integration/2025-06-12-przeglad")
    MarkdownNotesWriter(tmp_path).write(note)

    loaded = MarkdownNotesRepository(tmp_path).get(note.id)

    assert loaded is not None
    assert loaded.id == note.id
    assert loaded.metadata == note.metadata  # polskie znaki i listy zachowane
    assert loaded.body == note.body


def test_write_creates_missing_company_and_project_dirs(tmp_path: Path):
    note = _note("biap/workmate/2025-06-10-schemat")
    MarkdownNotesWriter(tmp_path).write(note)

    assert (tmp_path / "biap" / "workmate" / "2025-06-10-schemat.md").is_file()


def test_exists_reflects_written_note(tmp_path: Path):
    writer = MarkdownNotesWriter(tmp_path)
    note = _note("mpwik/scada-integration/2025-06-12-przeglad")

    assert writer.exists(note.id) is False

    writer.write(note)

    assert writer.exists(note.id) is True


def test_write_leaves_no_tmp_file(tmp_path: Path):
    note = _note("mpwik/scada-integration/2025-06-12-przeglad")
    MarkdownNotesWriter(tmp_path).write(note)

    # Zapis atomowy: plik tymczasowy '.tmp' nie może zostać po udanym zapisie.
    assert list(tmp_path.rglob("*.tmp")) == []


def test_failed_link_raises_write_error_and_leaves_no_partial_note(tmp_path: Path, monkeypatch):
    note = _note("mpwik/scada-integration/2025-06-12-przeglad")

    def boom(src, dst):
        raise OSError("symulowana awaria I/O")

    monkeypatch.setattr(writer_module.os, "link", boom)

    # Błąd I/O jest opakowany w WriteError (granica MCP degraduje łagodnie).
    with pytest.raises(WriteError):
        MarkdownNotesWriter(tmp_path).write(note)

    # Czytelnik zachłanny (all()) nie może zobaczyć częściowego pliku notatki:
    # docelowa ścieżka nie powstaje, a plik tymczasowy jest sprzątany.
    assert not (tmp_path / f"{note.id}.md").exists()
    assert list(tmp_path.rglob("*.tmp")) == []


def test_write_rejects_note_id_escaping_notes_dir(tmp_path: Path):
    # Obrona w głąb (#9.4): nawet gdyby note.id ominął slugifikację serwisu wyżej, writer sam
    # odrzuca ucieczkę poza notes_dir zamiast pisać poza bazą wiedzy.
    outside_target = tmp_path.parent / "evil.md"
    note = _note("../evil")

    with pytest.raises(WriteError):
        MarkdownNotesWriter(tmp_path).write(note)

    assert not outside_target.exists()


def test_exists_rejects_note_id_escaping_notes_dir(tmp_path: Path):
    with pytest.raises(WriteError):
        MarkdownNotesWriter(tmp_path).exists("../../etc/passwd")


def test_write_never_overwrites_existing_note(tmp_path: Path):
    writer = MarkdownNotesWriter(tmp_path)
    note_id = "mpwik/scada-integration/2025-06-12-przeglad"
    writer.write(_note(note_id, body="pierwsza wersja"))

    # Create-only: druga notatka pod tym samym id to błąd, nie ciche nadpisanie.
    with pytest.raises(WriteError):
        writer.write(_note(note_id, body="druga wersja"))

    loaded = MarkdownNotesRepository(tmp_path).get(note_id)
    assert loaded is not None
    assert loaded.body == "pierwsza wersja"  # oryginał nietknięty


def test_temp_cleanup_failure_does_not_mask_note_exists_error(tmp_path: Path, monkeypatch):
    """``finally: tmp.unlink()`` bez osłony PODMIENIA ``NoteExistsError`` na błąd sprzątania.

    Na ``NoteExistsError`` stoi idempotencja notatki ze spotkania (ADR 0043): ścieżka równoległa
    rozpoznaje po nim „już złożona" i kończy pominięciem. Gdy sprzątanie półproduktu padnie
    (uchwyt trzymany przez inny proces, katalog RO), z ``finally`` wychodzi ``OSError`` i notatka
    ze spotkania raportuje twardą porażkę zamiast pominięcia. Poprawny kształt jest dwie funkcje
    niżej — w ``_atomic_replace``.
    """
    note_id = "mpwik/scada-integration/2025-06-12-przeglad"
    writer = MarkdownNotesWriter(tmp_path)
    writer.write(_note(note_id, body="pierwsza"))

    prawdziwy_unlink = Path.unlink

    def unlink_ktory_pada(self, missing_ok=False):
        if self.name.endswith(".tmp"):
            raise PermissionError("plik tymczasowy trzymany przez inny proces")
        return prawdziwy_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink_ktory_pada)

    with pytest.raises(NoteExistsError):
        writer.write(_note(note_id, body="druga"))
