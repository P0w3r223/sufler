"""Testy adapterów mutacji: pisarz (nadpisanie/usunięcie), migawki, rejestr zapowiedzi (ADR 0065).

Realny system plików (``tmp_path``) — sedno tych klas siedzi w atomowości i w tym, czego
ODMAWIAJĄ, więc atrapa systemu plików sprawdzałaby wyłącznie własną atrapę.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from workmate.adapters.outbound.filesystem_snapshots import FilesystemNoteSnapshots
from workmate.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
from workmate.adapters.outbound.memory_confirmations import InMemoryConfirmations
from workmate.core.domain.models import Note, NoteMetadata
from workmate.core.errors import WriteError

_META = NoteMetadata(title="Ustalenia", project="mpwik", date="2026-08-01")


def _note(body: str = "treść") -> Note:
    return Note(id="biap/mpwik/2026-08-01-ustalenia", metadata=_META, body=body)


def test_overwrite_replaces_content_of_an_existing_note(tmp_path):
    writer = MarkdownNotesWriter(tmp_path)
    writer.write(_note("pierwsza"))

    writer.overwrite(_note("druga"))

    plik = tmp_path / "biap/mpwik/2026-08-01-ustalenia.md"
    assert "druga" in plik.read_text(encoding="utf-8")
    assert "pierwsza" not in plik.read_text(encoding="utf-8")


def test_overwrite_refuses_to_create_a_note_that_does_not_exist(tmp_path):
    """``overwrite`` ma ZMIENIAĆ. Gdyby tworzył, literówka w identyfikatorze rodziłaby po cichu
    nowy plik obok tego, który miał być poprawiony."""
    writer = MarkdownNotesWriter(tmp_path)

    with pytest.raises(WriteError, match="nie istnieje"):
        writer.overwrite(_note())


def test_write_still_refuses_to_overwrite(tmp_path):
    """REGRESJA: create-only ``write`` zostaje nietknięte — na nim stoi idempotencja notatek
    ze spotkań i wątków."""
    from workmate.core.errors import NoteExistsError

    writer = MarkdownNotesWriter(tmp_path)
    writer.write(_note("pierwsza"))

    with pytest.raises(NoteExistsError):
        writer.write(_note("druga"))


def test_delete_removes_the_file_but_not_the_directory(tmp_path):
    writer = MarkdownNotesWriter(tmp_path)
    writer.write(_note())

    writer.delete(_note().id)

    assert not (tmp_path / "biap/mpwik/2026-08-01-ustalenia.md").exists()
    assert (tmp_path / "biap/mpwik").is_dir()  # katalogów nie ruszamy nawet pustych


def test_delete_refuses_a_missing_note(tmp_path):
    writer = MarkdownNotesWriter(tmp_path)

    with pytest.raises(WriteError, match="nie istnieje"):
        writer.delete("biap/mpwik/nie-ma")


def test_snapshot_lands_outside_the_notes_tree_and_keeps_the_content(tmp_path):
    """Migawki POZA ``notes_dir``: agent czyta katalog notatek zachłannie, więc kopie w środku
    wracałyby jako wyniki wyszukiwania i mnożyły odpowiedzi."""
    snapshots = FilesystemNoteSnapshots(tmp_path / "snapshots")

    gdzie = snapshots.save("biap/mpwik/2026-08-01-ustalenia", "stara treść")

    assert "snapshots" in gdzie
    assert (tmp_path / "snapshots").exists()
    from pathlib import Path

    assert Path(gdzie).read_text(encoding="utf-8") == "stara treść"


def test_snapshot_flattens_the_note_id_into_one_path_segment(tmp_path):
    """Bez spłaszczenia migawki tworzyłyby drzewo lustrzane do bazy wiedzy, a ``..`` w
    identyfikatorze wyprowadziłby zapis poza katalog kopii."""
    snapshots = FilesystemNoteSnapshots(tmp_path / "snapshots")

    gdzie = snapshots.save("../../ucieczka/notatka", "treść")

    from pathlib import Path

    assert Path(gdzie).resolve().is_relative_to((tmp_path / "snapshots").resolve())


def test_snapshot_failure_is_loud(tmp_path):
    """Cicha porażka kopii byłaby najgorszym możliwym zachowaniem — bramka nie miałaby jak
    odmówić operacji, którą kopia miała zabezpieczyć."""
    plik_zamiast_katalogu = tmp_path / "snapshots"
    plik_zamiast_katalogu.write_text("nie jestem katalogiem", encoding="utf-8")
    snapshots = FilesystemNoteSnapshots(plik_zamiast_katalogu)

    with pytest.raises(WriteError):
        snapshots.save("biap/mpwik/x", "treść")


def test_confirmation_expires_with_time():
    """Zapowiedź ma żyć minuty. Pytanie „czy potwierdzić skasowanie" zadane wczoraj nie może
    autoryzować operacji dzisiaj."""
    teraz = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    zegar = lambda: teraz  # noqa: E731
    ledger = InMemoryConfirmations(ttl=timedelta(minutes=15), clock=zegar)
    ledger.remember("klucz")

    assert ledger.seen("klucz")

    teraz = teraz + timedelta(minutes=16)
    assert not ledger.seen("klucz")


def test_confirmation_is_forgotten_after_use():
    ledger = InMemoryConfirmations()
    ledger.remember("klucz")
    ledger.forget("klucz")

    assert not ledger.seen("klucz")


def test_ledger_has_a_ceiling_so_the_model_cannot_grow_it_without_end():
    """Rejestr karmi model, więc bez sufitu wystarczyłaby pętla proszenia o mutacje."""
    ledger = InMemoryConfirmations(max_entries=3)

    for i in range(10):
        ledger.remember(f"klucz-{i}")

    assert sum(ledger.seen(f"klucz-{i}") for i in range(10)) <= 3
    assert ledger.seen("klucz-9")  # najnowsza zapowiedź przeżywa
