"""Bezpieczeństwo: path traversal nie pozwala sięgnąć poza katalog notatek.

To najważniejszy wektor „przejęcia danych poufnych": gdyby ``get_note`` dało się
namówić na odczyt pliku spoza ``notes_dir`` (np. tokeny HTTP z ADR 0007, ``.env``,
``/etc/passwd``), agent mógłby wyprowadzić sekret. Testy pilnują, że granica trzyma
po stronie ODCZYTU (``MarkdownNotesRepository.get``) i ZAPISU (``note_id``, ``write``).

Czasowniki ZMIENIAJĄCE notatkę (``delete``/``overwrite``/``digest``, ADR 0065) mają własną
sondę w ``tests/adapters/test_note_mutation_adapters.py``; tu domykamy ``write`` — czasownik
tworzący, jedyny z rodziny, którego żadna sonda dotąd nie badała.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from workmate.adapters.outbound.markdown_notes_repo import MarkdownNotesRepository
from workmate.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
from workmate.core.domain.models import Note, NoteMetadata
from workmate.core.domain.paths import note_id
from workmate.core.errors import WriteError

_SEKRET = "---\ntitle: x\n---\nSUPER-TAJNE"


def _baza_z_sekretem_obok(tmp_path: Path) -> tuple[Path, Path]:
    """Katalog notatek + realny „sekret" POZA nim (jako ``.md``, żeby sonda była ostra)."""
    notes = tmp_path / "notes"
    notes.mkdir()
    secret = tmp_path / "secret.md"
    secret.write_text(_SEKRET, encoding="utf-8")
    return notes, secret


def test_get_note_blocks_traversal_even_when_target_exists(tmp_path: Path):
    notes, _secret = _baza_z_sekretem_obok(tmp_path)
    repo = MarkdownNotesRepository(notes)

    for evil in ("../secret", "../../secret", "../../../../etc/passwd", "..\\secret"):
        assert repo.get(evil) is None


@pytest.mark.parametrize(
    "bezwzgledny",
    ["/etc/passwd", "//etc/passwd", "C:/Windows/win.ini", "\\\\serwer\\udzial\\plik"],
)
def test_get_note_blocks_absolute_identifier(tmp_path: Path, bezwzgledny: str):
    """Identyfikator BEZWZGLĘDNY to drugi kształt ucieczki — ``Path.__truediv__`` po prostu
    porzuca lewą stronę, więc ``notes_dir / "/etc/passwd"`` daje ``/etc/passwd``, bez ani jednej
    kropki, na której mogłaby się zatrzymać sonda pilnująca ``..``."""
    notes, _secret = _baza_z_sekretem_obok(tmp_path)

    assert MarkdownNotesRepository(notes).get(bezwzgledny) is None


def test_get_note_blocks_symlink_escape(tmp_path: Path):
    """Dowiązanie WEWNĄTRZ bazy wiedzy celujące na zewnątrz: nazwa nie ma ``..``, a plik ma.

    Realny scenariusz na flocie: wolumen bazy wiedzy montowany obok innych katalogów stanu.
    Granica stoi na ``resolve()``, więc ma to łapać — sonda to przypina.
    """
    notes, secret = _baza_z_sekretem_obok(tmp_path)
    link = notes / "skrot.md"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("tworzenie dowiązań wymaga uprawnień (Windows bez trybu dewelopera)")

    assert MarkdownNotesRepository(notes).get("skrot") is None


def test_write_refuses_to_escape_the_notes_directory(tmp_path: Path):
    """Zapis też jest granicą: ``write`` z ucieczką ma ODMÓWIĆ, a nie stworzyć plik obok bazy."""
    notes, _secret = _baza_z_sekretem_obok(tmp_path)
    ofiara = tmp_path / "poza-baza"
    nota = Note(
        id="../poza-baza",
        metadata=NoteMetadata(title="Tytul", project="p", date=date(2025, 1, 1)),
        body="tresc",
    )

    with pytest.raises(WriteError, match="poza katalogiem"):
        MarkdownNotesWriter(notes).write(nota)

    assert not ofiara.with_suffix(".md").exists()


def test_note_id_rejects_traversal_in_company_or_project():
    for company, project in (
        ("../etc", "p"),
        ("mpwik", "../.."),
        ("a/b", "p"),
        ("mpwik", "p\\x"),
        ("mpwik", ".."),
        ("", "p"),  # pusty segment złożyłby ścieżkę o poziom wyżej niż zamierzona
        ("mpwik", ""),
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
