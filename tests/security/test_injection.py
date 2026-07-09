"""Bezpieczeństwo: wstrzyknięcia do „bazy" (pliki notatek) są neutralizowane.

Trzy wektory: (1) treść notatki udająca frontmatter nie może podmienić metadanych;
(2) tytuł z ładunkiem YAML round-trypuje jako zwykły string (``safe_dump`` cytuje);
(3) strażnik ``reject_dangerous_content`` odrzuca NUL i znaki sterujące. Round-tripy
idą przez PRAWDZIWE adaptery zapisu/odczytu na ``tmp_path`` (nie ruszają ``data/``).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from workmate.adapters.outbound.markdown_notes_repo import MarkdownNotesRepository
from workmate.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
from workmate.core.domain.models import Note, NoteMetadata
from workmate.core.domain.sanitize import reject_dangerous_content
from workmate.core.errors import WriteError


def _note(note_id: str, *, title: str = "Tytul", body: str = "tresc") -> Note:
    meta = NoteMetadata(title=title, project="p", date=date(2025, 1, 1))
    return Note(id=note_id, metadata=meta, body=body)


def test_body_cannot_inject_frontmatter(tmp_path: Path):
    writer = MarkdownNotesWriter(tmp_path)
    repo = MarkdownNotesRepository(tmp_path)
    evil_body = "Normalna tresc.\n---\ninjected: true\nproject: cudzy-projekt\n---\ndalej"
    writer.write(_note("mpwik/p/2025-01-01-x", body=evil_body))

    back = repo.get("mpwik/p/2025-01-01-x")
    assert back is not None
    # Wstrzyknięty „frontmatter" zostaje w TREŚCI — nie zmienia metadanych.
    assert back.metadata.project == "p"
    assert "injected: true" in back.body


def test_title_yaml_payload_roundtrips_as_plain_string(tmp_path: Path):
    writer = MarkdownNotesWriter(tmp_path)
    repo = MarkdownNotesRepository(tmp_path)
    evil_title = "Tytul\nproject: cudzy-projekt\nowned: yes"
    writer.write(_note("mpwik/p/2025-01-01-y", title=evil_title))

    back = repo.get("mpwik/p/2025-01-01-y")
    assert back is not None
    # Cała wartość wraca jako jeden string tytułu; nie „wycieka" do innych pól.
    assert back.metadata.title == evil_title
    assert back.metadata.project == "p"


def test_reject_dangerous_content_blocks_nul_and_control_chars():
    with pytest.raises(WriteError):
        reject_dangerous_content("ok", "zla\x00tresc")  # bajt zerowy
    with pytest.raises(WriteError):
        reject_dangerous_content("ty\x07tul")  # znak sterujący (bell)


def test_reject_dangerous_content_allows_normal_whitespace():
    # Nowa linia / tab / CR są dozwolone — nie rzuca.
    reject_dangerous_content("linia1\nlinia2\ttab", "tresc\r\nOK")
