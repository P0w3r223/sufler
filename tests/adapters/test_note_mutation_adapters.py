"""Testy adapterów mutacji: pisarz (nadpisanie/usunięcie), migawki, rejestr zapowiedzi (ADR 0065).

Realny system plików (``tmp_path``) — sedno tych klas siedzi w atomowości i w tym, czego
ODMAWIAJĄ, więc atrapa systemu plików sprawdzałaby wyłącznie własną atrapę.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

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

    writer.overwrite_body(_note().id, "druga", expected_sha256=writer.digest(_note().id))

    plik = tmp_path / "biap/mpwik/2026-08-01-ustalenia.md"
    assert "druga" in plik.read_text(encoding="utf-8")
    assert "pierwsza" not in plik.read_text(encoding="utf-8")


def test_overwrite_refuses_to_create_a_note_that_does_not_exist(tmp_path):
    """``overwrite`` ma ZMIENIAĆ. Gdyby tworzył, literówka w identyfikatorze rodziłaby po cichu
    nowy plik obok tego, który miał być poprawiony."""
    writer = MarkdownNotesWriter(tmp_path)

    with pytest.raises(WriteError, match="nie istnieje"):
        writer.overwrite_body(_note().id, "treść", expected_sha256="cokolwiek")


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

    writer.delete(_note().id, expected_sha256=writer.digest(_note().id))

    assert not (tmp_path / "biap/mpwik/2026-08-01-ustalenia.md").exists()
    assert (tmp_path / "biap/mpwik").is_dir()  # katalogów nie ruszamy nawet pustych


def test_delete_refuses_a_missing_note(tmp_path):
    writer = MarkdownNotesWriter(tmp_path)

    with pytest.raises(WriteError, match="nie istnieje"):
        writer.delete("biap/mpwik/nie-ma", expected_sha256="cokolwiek")


def test_snapshot_lands_outside_the_notes_tree_and_keeps_the_content(tmp_path):
    """Migawki POZA ``notes_dir``: agent czyta katalog notatek zachłannie, więc kopie w środku
    wracałyby jako wyniki wyszukiwania i mnożyły odpowiedzi."""
    snapshots = FilesystemNoteSnapshots(tmp_path / "snapshots")

    gdzie = snapshots.save(_note().id, "---\ntitle: Ustalenia\n---\n\nstara treść\n")

    assert "snapshots" in gdzie
    assert (tmp_path / "snapshots").exists()
    from pathlib import Path

    assert "stara treść" in Path(gdzie).read_text(encoding="utf-8")


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
        snapshots.save(_note().id, "treść")


def test_confirmation_expires_with_time():
    """Zapowiedź ma żyć minuty. Pytanie „czy potwierdzić skasowanie" zadane wczoraj nie może
    autoryzować operacji dzisiaj."""
    teraz = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    zegar = lambda: teraz  # noqa: E731
    ledger = InMemoryConfirmations(ttl=timedelta(minutes=15), clock=zegar)
    ledger.remember("klucz", "tura-1")

    assert ledger.turn_of("klucz") == "tura-1"

    teraz = teraz + timedelta(minutes=16)
    assert ledger.turn_of("klucz") is None


def test_confirmation_is_forgotten_after_use():
    ledger = InMemoryConfirmations()
    ledger.remember("klucz", "tura-1")
    ledger.forget("klucz")

    assert ledger.turn_of("klucz") is None


def test_ledger_has_a_ceiling_so_the_model_cannot_grow_it_without_end():
    """Rejestr karmi model, więc bez sufitu wystarczyłaby pętla proszenia o mutacje."""
    ledger = InMemoryConfirmations(max_entries=3)

    for i in range(10):
        ledger.remember(f"klucz-{i}", f"tura-{i}")

    assert sum(ledger.turn_of(f"klucz-{i}") is not None for i in range(10)) <= 3
    assert ledger.turn_of("klucz-9") == "tura-9"  # najnowsza zapowiedź przeżywa


def test_new_verbs_refuse_to_escape_the_notes_directory(tmp_path):
    """ADR 0065 §2 mówi wprost: „a `delete` verb that forgets it would be unprotected".

    Anty-traversal siedzi w adapterze i KAŻDY nowy czasownik musi go zawołać — sonda pilnuje
    tego osobno dla obu, bo pre-istniejąca badała wyłącznie ``exists``.
    """
    writer = MarkdownNotesWriter(tmp_path)
    ofiara = tmp_path.parent / "poza-baza.md"
    ofiara.write_text("cudza treść", encoding="utf-8")

    with pytest.raises(WriteError, match="poza katalogiem"):
        writer.delete("../poza-baza", expected_sha256="x")
    with pytest.raises(WriteError, match="poza katalogiem"):
        writer.overwrite_body("../poza-baza", "podmiana", expected_sha256="x")
    with pytest.raises(WriteError, match="poza katalogiem"):
        writer.digest("../poza-baza")

    assert ofiara.read_text(encoding="utf-8") == "cudza treść"


def test_overwrite_refuses_when_the_note_changed_since_it_was_read(tmp_path):
    """Między odczytem a zapisem leży wywołanie sędziego, a drzwi obsługują tury równolegle.

    Bez kontroli wersji druga zmiana nadpisywała pierwszą, a wersja pośrednia znikała BEZ
    MIGAWKI — czyli w jedynym stanie, którego ta warstwa ma nie dopuszczać.
    """
    writer = MarkdownNotesWriter(tmp_path)
    writer.write(_note("wersja 0"))
    wersja0 = writer.digest(_note().id)

    writer.overwrite_body(_note().id, "wersja 1", expected_sha256=wersja0)  # pierwsza tura zdążyła

    with pytest.raises(WriteError, match="zmieniła się od odczytu"):
        # druga tura miała stary skrót
        writer.overwrite_body(_note().id, "wersja 2", expected_sha256=wersja0)

    assert "wersja 1" in (tmp_path / "biap/mpwik/2026-08-01-ustalenia.md").read_text(
        encoding="utf-8"
    )


def test_snapshot_is_a_byte_copy_of_the_file_including_fields_outside_the_schema(tmp_path):
    """Migawka ma być KOPIĄ PLIKU, nie renderem z modelu — i to jest cała ta poprawka.

    Poprzednia redakcja składała kopię przez ``render_note``, czyli z ``NoteMetadata``, a ten
    jest pydantikiem z domyślnym ``extra="ignore"``. Pola dopisane ręcznie do frontmatteru
    (``status:``, ``source_url:``) i komentarze YAML **znikały z kopii bez śladu**. Przy ``edit``
    ratowała jeszcze nocna kopia wolumenu; przy ``delete`` migawka jest JEDYNYM odzyskiem między
    kopiami, więc strata była nieodwracalna — a wyglądała jak udane zabezpieczenie.

    Sonda idzie CAŁĄ ścieżką (plik → ``content_with_digest`` → migawka), bo dokładnie na jej
    styku ginęły te pola: adapter migawek nigdy ich nie widział.
    """
    from pathlib import Path as _Path

    plik = tmp_path / "biap" / "mpwik" / "2026-08-01-ustalenia.md"
    plik.parent.mkdir(parents=True)
    oryginal = (
        "---\n"
        "# uzupełnione ręcznie 2026-08-19\n"
        "title: Ustalenia\n"
        "project: mpwik\n"
        "date: 2026-08-01\n"
        "status: do-weryfikacji\n"
        "source_url: https://przyklad/12\n"
        "---\n"
        "\n"
        "treść\n"
    )
    # ``newline=""`` także tutaj: sonda porównuje SKRÓT bajtów pliku, więc atrapa zapisana
    # z translacją mierzyłaby na Windows własny sposób zapisu, a nie adapter migawek.
    plik.write_text(oryginal, encoding="utf-8", newline="")  # atrapa = dokładnie te bajty
    writer = MarkdownNotesWriter(tmp_path)
    snapshots = FilesystemNoteSnapshots(tmp_path / "snapshots")

    tresc, skrot = writer.content_with_digest(_note().id)
    gdzie = snapshots.save(_note().id, tresc)

    # Przez ``read_bytes``, nie ``read_text``: tryb tekstowy zamienia CRLF z powrotem na LF
    # przy ODCZYCIE, więc migawka zapisana z translacją przechodziła asercję nazwaną
    # „CO DO BAJTU". Sonda pilnowała wtedy niezmiennika wyłącznie z nazwy.
    assert _Path(gdzie).read_bytes() == oryginal.encode("utf-8")  # CO DO BAJTU
    assert skrot == hashlib.sha256(oryginal.encode("utf-8")).hexdigest()


def test_content_with_digest_refuses_instead_of_guessing_when_there_is_nothing_to_copy(tmp_path):
    """Pusty skrót znaczy „nie ma czego zabezpieczyć" i bramka mutacji czyta to jako odmowę.

    Zwracanie samej treści bez skrótu (albo odwrotnie) dawałoby wołającemu wybór między mutacją
    bez kopii a kopią bez kontroli wersji — dwa stany, których ta warstwa ma nie dopuszczać.
    """
    writer = MarkdownNotesWriter(tmp_path)

    assert writer.content_with_digest("biap/mpwik/nie-ma") == ("", "")

    plik = tmp_path / "biap" / "mpwik" / "krzaki.md"
    plik.parent.mkdir(parents=True)
    plik.write_bytes(b"---\ntitle: \xff\xfe niepoprawny UTF-8\n---\n")

    assert writer.content_with_digest("biap/mpwik/krzaki") == ("", "")


def test_delete_refuses_when_the_note_changed_since_it_was_read(tmp_path):
    """Kasowanie też dzieli odczyt od zapisu oceną sędziego, a drzwi obsługują tury równolegle.

    Migawka zabezpiecza wersję, którą przeczytaliśmy PRZED oceną. Gdy w oknie oczekiwania ktoś
    notatkę zmienił, skasowanie bez kontroli wersji zabrałoby wersję pośrednią — tę, której żadna
    kopia nie trzyma. Ta sama kontrola co w ``overwrite``.
    """
    writer = MarkdownNotesWriter(tmp_path)
    writer.write(_note("pierwsza"))
    wersja_z_odczytu = writer.digest(_note().id)
    writer.overwrite_body(_note().id, "zmiana w oknie sędziego", expected_sha256=wersja_z_odczytu)

    with pytest.raises(WriteError, match="zmieniła się od odczytu"):
        writer.delete(_note().id, expected_sha256=wersja_z_odczytu)

    assert (tmp_path / "biap/mpwik/2026-08-01-ustalenia.md").exists()
