"""Testy importera seed korpusu (W0, ``sufler-seed-corpus``).

Czysta część (``derive_note``, ``apply_seed``, ``format_report``) na atrapach w pamięci
(``FakeNotesWriter`` / ``FakeProjectsRepository``) — bez sieci i FS. Kluczowe niezmienniki:
domyślny dry-run nic nie zapisuje, dedup po deterministycznym id, powtórny przebieg pomija.
"""

from __future__ import annotations

from datetime import date

from sufler.adapters.inbound.seed_corpus import (
    ACTION_COLLISION,
    ACTION_CREATED,
    ACTION_SKIP,
    ACTION_WOULD_CREATE,
    _load_documents,
    apply_seed,
    derive_note,
    format_report,
)
from sufler.core.application.services import NotesWriteService
from sufler.core.domain.models import Project
from tests.conftest import FakeNotesWriter, FakeProjectsRepository

_FALLBACK = date(2025, 1, 1)


def _projects() -> FakeProjectsRepository:
    return FakeProjectsRepository(
        [Project(key="workmate", company="biap", name="Sufler", description="Asystent")],
        records={},
    )


def _service(writer: FakeNotesWriter) -> NotesWriteService:
    return NotesWriteService(writer, _projects())


# --- derive_note (czysta) -------------------------------------------------------


def test_derive_note_uses_h1_title_and_date_header():
    text = "# 0006. Bramka zapisu\n\nDate: 2026-07-07\nStatus: accepted\n\nTreść ADR."
    metadata, body = derive_note(
        "docs/adr/0006-gate.md", text, project="workmate", fallback_date=_FALLBACK
    )
    assert metadata.title == "0006. Bramka zapisu"
    assert metadata.date == date(2026, 7, 7)
    assert metadata.project == "workmate"
    assert body.startswith("# 0006.")


def test_derive_note_falls_back_to_filename_and_fallback_date():
    metadata, _ = derive_note(
        "README.md", "Bez nagłówka H1 ani daty.", project="workmate", fallback_date=_FALLBACK
    )
    assert metadata.title == "README"
    assert metadata.date == _FALLBACK


def test_derive_note_records_provenance_in_tags_not_metadata_fields():
    metadata, _ = derive_note(
        "docs/adr/0006-gate.md", "# T\n", project="workmate", fallback_date=_FALLBACK
    )
    assert "seed" in metadata.tags
    assert "adr" in metadata.tags
    assert "src:docs/adr/0006-gate.md" in metadata.tags


# --- apply_seed: dry-run / zapis / dedup ---------------------------------------


def _docs() -> list[tuple[str, str]]:
    return [
        ("README.md", "# Sufler\n\nOpis systemu."),
        ("docs/adr/0006-gate.md", "# 0006. Bramka\n\nDate: 2026-07-07\n\nTreść."),
    ]


def test_dry_run_writes_nothing_and_marks_would_create():
    writer = FakeNotesWriter()
    results = apply_seed(
        _docs(),
        writer=writer,
        service=_service(writer),
        company="biap",
        project="workmate",
        fallback_date=_FALLBACK,
        write=False,
    )
    assert writer.saved == {}
    assert all(r.action == ACTION_WOULD_CREATE for r in results)


def test_write_creates_notes_at_deterministic_ids():
    writer = FakeNotesWriter()
    results = apply_seed(
        _docs(),
        writer=writer,
        service=_service(writer),
        company="biap",
        project="workmate",
        fallback_date=_FALLBACK,
        write=True,
    )
    ids = {r.note_id for r in results}
    assert "biap/workmate/2025-01-01-sufler" in ids
    assert "biap/workmate/2026-07-07-0006-bramka" in ids
    assert all(r.action == ACTION_CREATED for r in results)
    assert set(writer.saved) == ids


def test_second_run_is_idempotent_skips_existing():
    writer = FakeNotesWriter()
    service = _service(writer)
    common = dict(
        writer=writer,
        service=service,
        company="biap",
        project="workmate",
        fallback_date=_FALLBACK,
    )
    apply_seed(_docs(), write=True, **common)
    before = dict(writer.saved)

    again = apply_seed(_docs(), write=True, **common)

    assert writer.saved == before  # żadnego duplikatu -2
    assert all(r.action == ACTION_SKIP for r in again)


def test_intra_batch_collision_is_faithful_dry_run_vs_write():
    """Dwa dokumenty → ten sam id: drugi to kolizja, a podgląd zgadza się z zapisem."""
    # Bez H1 i bez Date: oba dają tytuł z nazwy pliku (README) + tę samą datę fallback → ten sam id.
    colliding = [("a/README.md", "Bez nagłówka."), ("b/README.md", "Też bez nagłówka.")]
    common = dict(company="biap", project="workmate", fallback_date=_FALLBACK)

    dry_writer = FakeNotesWriter()
    dry = apply_seed(
        colliding, writer=dry_writer, service=_service(dry_writer), write=False, **common
    )
    assert [r.action for r in dry] == [ACTION_WOULD_CREATE, ACTION_COLLISION]

    wet_writer = FakeNotesWriter()
    wet = apply_seed(
        colliding, writer=wet_writer, service=_service(wet_writer), write=True, **common
    )
    assert [r.action for r in wet] == [ACTION_CREATED, ACTION_COLLISION]
    assert len(wet_writer.saved) == 1  # kolizja NIE utworzyła drugiej notatki


# --- _load_documents: dyspozycja formatów i pominięcia -------------------------


def test_load_documents_reads_text_and_extracts_office(tmp_path):
    """Katalog md/txt/docx → wszystkie wczytane jako (ścieżka, tekst); binarny ekstrahowany."""
    from docx import Document

    (tmp_path / "notatka.md").write_text("# Tytuł\n\ntreść", encoding="utf-8")
    (tmp_path / "plik.txt").write_text("zwykły tekst", encoding="utf-8")
    doc = Document()
    doc.add_paragraph("Ustalenia z Worda")
    doc.save(tmp_path / "spec.docx")

    documents, skipped = _load_documents(tmp_path, "*", recursive=False)

    by_path = dict(documents)
    assert by_path["notatka.md"].startswith("# Tytuł")
    assert by_path["plik.txt"] == "zwykły tekst"
    assert "Ustalenia z Worda" in by_path["spec.docx"]
    assert skipped == []


def test_load_documents_skips_unsupported_and_corrupt_with_reason(tmp_path):
    """Nieobsługiwane rozszerzenie i uszkodzony docx → 'pominięte', bez wywracania partii."""
    (tmp_path / "ok.md").write_text("dobra notatka", encoding="utf-8")
    (tmp_path / "obraz.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "zepsuty.docx").write_bytes(b"to nie jest zip")

    documents, skipped = _load_documents(tmp_path, "*", recursive=False)

    assert [src for src, _ in documents] == ["ok.md"]
    reasons = dict(skipped)
    assert set(reasons) == {"obraz.png", "zepsuty.docx"}
    assert "nieobsługiwane" in reasons["obraz.png"]  # powód niesie rozszerzenie
    assert reasons["zepsuty.docx"]  # uszkodzony plik → niepusty powód (nie wywraca partii)


def test_report_summarizes_actions():
    results = apply_seed(
        _docs(),
        writer=FakeNotesWriter(),
        service=_service(FakeNotesWriter()),
        company="biap",
        project="workmate",
        fallback_date=_FALLBACK,
        write=False,
    )
    report = format_report(results, write=False)
    assert "DRY-RUN" in report
    assert "do utworzenia 2" in report
