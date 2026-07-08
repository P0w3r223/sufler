"""Testy repozytorium notatek Markdown (parsowanie frontmatter, błędy)."""
from __future__ import annotations

from pathlib import Path

import pytest

from workmate.adapters.outbound.markdown_notes_repo import (
    MarkdownNotesRepository,
    NoteParseError,
)
from workmate.core.errors import RepositoryError

VALID_NOTE = """---
title: Przykładowa notatka
project: mpwik
date: 2025-06-12
participants: [Anna Kowalska, Marek Nowak]
decisions:
  - Zatwierdzono zakres MVP.
action_items:
  - Przygotować szkic API.
open_questions:
  - Czy potrzebne środowisko testowe?
tags: [api, kickoff]
---

Treść notatki. Zawiera nawet poziomą linię:

---

I dalszy tekst po linii poziomej.
"""


def _write(dir_path: Path, rel: str, content: str) -> None:
    path = dir_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_all_parses_note(tmp_path: Path):
    _write(tmp_path, "mpwik/2025-06-12-przyklad.md", VALID_NOTE)
    repo = MarkdownNotesRepository(tmp_path)

    notes = repo.all()

    assert len(notes) == 1
    note = notes[0]
    assert note.id == "mpwik/2025-06-12-przyklad"
    assert note.metadata.title == "Przykładowa notatka"
    assert note.metadata.participants == ["Anna Kowalska", "Marek Nowak"]
    # Pozioma linia '---' w treści nie może uciąć ciała notatki.
    assert "dalszy tekst po linii poziomej" in note.body


def test_get_returns_note_by_id(tmp_path: Path):
    _write(tmp_path, "biap/notatka.md", VALID_NOTE)
    repo = MarkdownNotesRepository(tmp_path)

    note = repo.get("biap/notatka")

    assert note is not None
    assert note.id == "biap/notatka"


def test_get_missing_returns_none(tmp_path: Path):
    tmp_path.joinpath("notes").mkdir()
    repo = MarkdownNotesRepository(tmp_path / "notes")

    assert repo.get("nie/istnieje") is None


def test_get_rejects_path_traversal(tmp_path: Path):
    # Prawdziwa notatka LEŻY poza katalogiem notatek...
    (tmp_path / "secret.md").write_text(VALID_NOTE, encoding="utf-8")
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    repo = MarkdownNotesRepository(notes_dir)

    # ...a spreparowane id próbujące ją wskazać nie może jej odczytać.
    assert repo.get("../secret") is None


def test_invalid_frontmatter_raises(tmp_path: Path):
    # Brak wymaganego pola 'title'.
    _write(tmp_path, "x/bad.md", "---\nproject: mpwik\ndate: 2025-06-12\n---\nTreść\n")
    repo = MarkdownNotesRepository(tmp_path)

    with pytest.raises(NoteParseError):
        repo.all()


def test_missing_frontmatter_fence_raises(tmp_path: Path):
    _write(tmp_path, "x/nofront.md", "Zwykły tekst bez frontmatter.\n")
    repo = MarkdownNotesRepository(tmp_path)

    with pytest.raises(NoteParseError):
        repo.all()


def test_missing_notes_dir_raises(tmp_path: Path):
    repo = MarkdownNotesRepository(tmp_path / "brak")

    with pytest.raises(RepositoryError):
        repo.all()
