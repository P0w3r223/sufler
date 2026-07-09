"""Bezpieczeństwo: path traversal nie pozwala sięgnąć poza katalog notatek.

To najważniejszy wektor „przejęcia danych poufnych": gdyby ``get_note`` dało się
namówić na odczyt pliku spoza ``notes_dir`` (np. tokeny HTTP z ADR 0007, ``.env``,
``/etc/passwd``), agent mógłby wyprowadzić sekret. Testy pilnują, że granica trzyma
po stronie ODCZYTU (``MarkdownNotesRepository.get``) i ZAPISU (``note_id``).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from workmate.adapters.outbound.markdown_notes_repo import MarkdownNotesRepository
from workmate.core.domain.paths import note_id


def test_get_note_blocks_traversal_even_when_target_exists(tmp_path: Path):
    notes = tmp_path / "notes"
    notes.mkdir()
    # Realny „sekret" POZA katalogiem notatek — gdyby granica nie działała,
    # get() by go wczytał. Kładziemy go jako .md, żeby test był ostry.
    secret = tmp_path / "secret.md"
    secret.write_text("---\ntitle: x\n---\nSUPER-TAJNE", encoding="utf-8")
    repo = MarkdownNotesRepository(notes)

    for evil in ("../secret", "../../secret", "../../../../etc/passwd", "..\\secret"):
        assert repo.get(evil) is None


def test_note_id_rejects_traversal_in_company_or_project():
    for company, project in (
        ("../etc", "p"),
        ("mpwik", "../.."),
        ("a/b", "p"),
        ("mpwik", "p\\x"),
        ("mpwik", ".."),
    ):
        with pytest.raises(ValueError):
            note_id(company, project, date(2025, 1, 1), "Tytul")


def test_note_id_slugifies_dangerous_title_to_safe_segment():
    nid = note_id("mpwik", "p", date(2025, 1, 1), "../../etc/passwd")
    tail = nid.rsplit("/", 1)[-1]
    # Tytuł-atak zostaje zeslugowany do [a-z0-9-]; żadnego '..' ani '/'.
    assert nid.startswith("mpwik/p/2025-01-01-")
    assert tail == "2025-01-01-etc-passwd"
    assert "/" not in tail and ".." not in tail
