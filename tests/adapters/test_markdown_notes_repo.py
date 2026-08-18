"""Testy repozytorium notatek Markdown (parsowanie frontmatter, błędy, cache)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest import mock

import pytest

from workmate.adapters.outbound.markdown_notes_repo import (
    MarkdownNotesRepository,
    NoteParseError,
)
from workmate.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
from workmate.adapters.outbound.yaml_projects_repo import YamlProjectsRepository
from workmate.core.application.services import NotesService, NotesWriteService
from workmate.core.domain.models import Note, NoteMetadata
from workmate.core.domain.notes import build_note_metadata
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


def test_parse_error_message_uses_relative_path_not_absolute(tmp_path: Path):
    # Brak wymaganego 'title' → NoteParseError. Komunikat musi wskazać notatkę
    # ścieżką WZGLĘDNĄ, bez ujawniania bezwzględnej struktury serwera.
    _write(tmp_path, "mpwik/bad.md", "---\nproject: mpwik\ndate: 2025-06-12\n---\nTreść\n")
    repo = MarkdownNotesRepository(tmp_path)

    with pytest.raises(NoteParseError) as exc:
        repo.all()

    msg = str(exc.value)
    assert "mpwik/bad.md" in msg
    assert str(tmp_path) not in msg


# --- Cache per plik z unieważnianiem fingerprintem (st_mtime_ns, st_size) --------


def _note(title: str, body: str) -> str:
    """Zbuduj minimalną, poprawną notatkę o zadanym tytule/treści (rozmiar zależny od treści)."""
    return f"---\ntitle: {title}\nproject: mpwik\ndate: 2025-06-12\n---\n\n{body}\n"


def test_second_all_reuses_cache_without_reparsing(tmp_path: Path):
    """Dwa ``all()`` bez zmian: identyczne notatki; ``_load`` woła N razy, potem 0."""
    _write(tmp_path, "mpwik/a.md", _note("A", "tresc A"))
    _write(tmp_path, "mpwik/b.md", _note("B", "tresc B"))
    repo = MarkdownNotesRepository(tmp_path)

    with mock.patch.object(repo, "_load", wraps=repo._load) as spy:
        first = repo.all()
        assert spy.call_count == 2
        second = repo.all()
        assert spy.call_count == 2  # drugi przebieg NIE parsuje ponownie

    assert {n.id for n in first} == {n.id for n in second}
    assert [n.body for n in first] == [n.body for n in second]


def test_new_file_in_existing_dir_is_parsed_on_next_all_only(tmp_path: Path):
    """Dopisanie NOWEGO pliku: drugie ``all()`` widzi go i parsuje TYLKO jego."""
    _write(tmp_path, "mpwik/a.md", _note("A", "tresc A"))
    repo = MarkdownNotesRepository(tmp_path)

    with mock.patch.object(repo, "_load", wraps=repo._load) as spy:
        repo.all()
        assert spy.call_count == 1
        _write(tmp_path, "mpwik/c.md", _note("C", "tresc C"))
        notes = repo.all()
        assert spy.call_count == 2  # doparsowano tylko nowy plik (a.md z cache)

    assert {n.id for n in notes} == {"mpwik/a", "mpwik/c"}


def test_modified_content_is_reparsed_and_reflects_new_body(tmp_path: Path):
    """Modyfikacja treści (inny rozmiar) unieważnia cache — ``all()`` zwraca nową treść."""
    _write(tmp_path, "mpwik/a.md", _note("A", "stara tresc"))
    repo = MarkdownNotesRepository(tmp_path)

    with mock.patch.object(repo, "_load", wraps=repo._load) as spy:
        assert repo.all()[0].body == "stara tresc"
        assert spy.call_count == 1
        _write(tmp_path, "mpwik/a.md", _note("A", "zupelnie nowa i dluzsza tresc"))
        reloaded = repo.all()
        assert spy.call_count == 2  # zmieniony plik przeparsowany ponownie

    assert reloaded[0].body == "zupelnie nowa i dluzsza tresc"


def test_deleted_file_is_evicted_from_results(tmp_path: Path):
    """Usunięcie pliku eksmituje notatkę z wyników (cache trzyma tylko aktualne ścieżki)."""
    _write(tmp_path, "mpwik/a.md", _note("A", "tresc A"))
    _write(tmp_path, "mpwik/b.md", _note("B", "tresc B"))
    repo = MarkdownNotesRepository(tmp_path)

    assert {n.id for n in repo.all()} == {"mpwik/a", "mpwik/b"}
    (tmp_path / "mpwik" / "b.md").unlink()

    assert {n.id for n in repo.all()} == {"mpwik/a"}


def test_warm_cache_sees_external_write_via_fingerprint(tmp_path: Path):
    """Zapis przez INNĄ ścieżkę (symulacja innego procesu) jest widoczny bez jawnego
    powiadomienia — repo z rozgrzanym cache wykrywa nowy plik po fingerprincie katalogu."""
    _write(tmp_path, "mpwik/a.md", _note("A", "tresc A"))
    repo = MarkdownNotesRepository(tmp_path)
    repo.all()  # rozgrzej cache

    # „Inny proces" dokłada notatkę przez writer (nie przez repo).
    writer = MarkdownNotesWriter(tmp_path)
    writer.write(
        Note(
            id="mpwik/proj/2025-01-01-nowa",
            metadata=NoteMetadata(title="Nowa", project="mpwik", date=date(2025, 1, 1)),
            body="tresc z innego procesu",
        )
    )

    ids = {n.id for n in repo.all()}
    assert "mpwik/proj/2025-01-01-nowa" in ids


def test_save_note_then_search_sees_fresh_note_on_same_dir(tmp_path: Path):
    """Integracja: ``save_note`` → ``search_notes`` na tym samym katalogu widzi świeżą notatkę."""
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "projects:\n"
        "  - key: workmate\n"
        "    company: biap\n"
        "    name: WorkMate\n"
        "    description: Asystent\n"
        "    status: active\n"
        "    health: green\n"
        "    phase: Faza 2\n"
        "    summary: Prace\n"
        "    last_updated: 2025-06-24\n",
        encoding="utf-8",
    )
    notes_repo = MarkdownNotesRepository(notes_dir)
    notes_service = NotesService(notes_repo)
    write_service = NotesWriteService(
        MarkdownNotesWriter(notes_dir), YamlProjectsRepository(registry)
    )

    # Rozgrzej cache na pustym katalogu (brak trafień).
    assert notes_service.search_notes("scada") == []

    write_service.save_note(
        build_note_metadata(title="Notatka o SCADA", project="workmate", date=date(2025, 1, 1)),
        "Ustalenia dotyczące integracji SCADA.",
    )

    hits = notes_service.search_notes("scada")
    assert any("scada" in hit.title.lower() for hit in hits)


def test_all_skips_a_note_that_disappears_between_the_walk_and_the_stat(tmp_path: Path):
    """Od ADR 0065 kasowanie notatki jest realną drogą, więc wyścig ze spacerem ``rglob`` też.

    ``rglob`` oddaje ścieżki zebrane wcześniej; gdy notatka zniknie zanim dojdzie do ``stat``,
    surowy ``FileNotFoundError`` wywraca CAŁY odczyt bazy wiedzy (retrieval, statusy projektów),
    zamiast pominąć jeden plik, którego już nie ma.
    """
    notes_dir = tmp_path / "notes"
    (notes_dir / "mpwik" / "scada").mkdir(parents=True)
    (notes_dir / "mpwik" / "scada" / "zostaje.md").write_text(VALID_NOTE, encoding="utf-8")
    znikajaca = notes_dir / "mpwik" / "scada" / "znika.md"
    znikajaca.write_text(VALID_NOTE, encoding="utf-8")

    prawdziwy_stat = Path.stat

    def stat_po_kasacji(self, *args, **kwargs):
        if self.name == "znika.md":
            znikajaca.unlink(missing_ok=True)  # kasowanie zdążyło się wykonać
        return prawdziwy_stat(self, *args, **kwargs)

    with mock.patch.object(Path, "stat", stat_po_kasacji):
        notes = MarkdownNotesRepository(notes_dir).all()

    assert [note.id for note in notes] == ["mpwik/scada/zostaje"]
