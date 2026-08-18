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


# --- notatka ze spotkania: id deterministyczny + idempotencja (ADR 0043) -----


def test_save_meeting_note_id_is_deterministic_from_ref_not_title():
    # id spotkania wywodzi się z meeting_ref, NIE ze slug tytułu Claude: ten sam ref (inny
    # tytuł) → ten sam id (klucz idempotencji), inny ref → inny id.
    svc_a = NotesWriteService(FakeNotesWriter(), _projects_repo())
    svc_b = NotesWriteService(FakeNotesWriter(), _projects_repo())
    svc_c = NotesWriteService(FakeNotesWriter(), _projects_repo())

    a = svc_a.save_meeting_note(_metadata(title="Przeglad"), body="t", meeting_ref="join-abc")
    b = svc_b.save_meeting_note(_metadata(title="Zupelnie inny"), body="t", meeting_ref="join-abc")
    c = svc_c.save_meeting_note(_metadata(title="Przeglad"), body="t", meeting_ref="join-XYZ")

    assert a.id.startswith("mpwik/scada-integration/2025-06-12-mtg-")
    assert b.id == a.id  # ten sam ref → ten sam id, mimo innego tytułu
    assert c.id != a.id  # inny ref → inny id


def test_meeting_note_id_precheck_reflects_existing():
    writer = FakeNotesWriter()
    service = NotesWriteService(writer, _projects_repo())

    on = date(2025, 6, 12)
    assert service.meeting_note_id("join-abc", project="scada-integration", date=on) is None
    saved = service.save_meeting_note(_metadata(), body="t", meeting_ref="join-abc")
    assert service.meeting_note_id("join-abc", project="scada-integration", date=on) == saved.id


def test_meeting_note_id_unknown_project_returns_none():
    service = NotesWriteService(FakeNotesWriter(), _projects_repo())
    assert service.meeting_note_id("ref", project="nieznany", date=date(2025, 6, 12)) is None


def test_save_meeting_note_is_create_only_on_repeat(tmp_path):
    # Z REALNYM create-only writerem: ponowny zapis tego samego spotkania rzuca (kolizja),
    # zamiast tworzyć duplikat -2 — to jest gwarancja idempotencji na poziomie FS (ADR 0043).
    from workmate.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter

    service = NotesWriteService(MarkdownNotesWriter(tmp_path), _projects_repo())
    service.save_meeting_note(_metadata(), body="pierwsza", meeting_ref="join-abc")

    with pytest.raises(WriteError):
        service.save_meeting_note(_metadata(title="Inny"), body="druga", meeting_ref="join-abc")


def test_require_project_raises_on_unknown_before_io():
    service = NotesWriteService(FakeNotesWriter(), _projects_repo())
    with pytest.raises(WriteError):
        service.require_project("nieznany")


def test_require_project_ok_for_known():
    service = NotesWriteService(FakeNotesWriter(), _projects_repo())
    service.require_project("scada-integration")  # nie rzuca


# --- Strażnik znaków sterujących na WSZYSTKICH trzech drogach tworzenia (reguła twarda #4) ---
# Sam strażnik ma testy jednostkowe (``tests/core/domain/test_sanitize.py``); tu chodzi o coś
# innego: że KAŻDA droga tworzenia notatki go faktycznie woła i robi to PRZED dotknięciem
# writera. Dotąd sprawdzała to wyłącznie ścieżka mutacji (ADR 0065), czyli ta, która powstała
# ostatnia — a reguła dotyczy przede wszystkim tych trzech, starszych.


@pytest.mark.parametrize(
    ("pole", "meta_kwargs", "body"),
    [
        ("tytuł", {"title": "Przeglad\x00API"}, "Treść."),
        ("treść", {}, "Treść z\x07dzwonkiem."),
    ],
    ids=["tytul", "tresc"],
)
def test_save_note_rejects_control_characters_before_touching_the_writer(pole, meta_kwargs, body):
    writer = FakeNotesWriter()
    service = NotesWriteService(writer, _projects_repo())

    with pytest.raises(WriteError, match="sterując"):
        service.save_note(_metadata(**meta_kwargs), body=body)

    assert writer.saved == {}, f"zapis {pole} przeszedł mimo znaku sterującego"


def test_save_note_checks_the_list_fields_too_not_only_title_and_body():
    """Uczestnicy, decyzje, tagi też lądują w PLIKU bazy — strażnik bierze je wszystkie razem."""
    writer = FakeNotesWriter()
    service = NotesWriteService(writer, _projects_repo())
    meta = NoteMetadata(
        title="Przeglad",
        project="scada-integration",
        date=date(2025, 6, 12),
        participants=["Anna Kowalska"],
        decisions=["wdrożenie\x1b[31m"],
    )

    with pytest.raises(WriteError, match="sterując"):
        service.save_note(meta, body="Treść.")

    assert writer.saved == {}


def test_save_meeting_note_rejects_control_characters():
    """Notatka ze spotkania powstaje z transkryptu Teams — treści NIE kontrolujemy."""
    writer = FakeNotesWriter()
    service = NotesWriteService(writer, _projects_repo())

    with pytest.raises(WriteError, match="sterując"):
        service.save_meeting_note(_metadata(), body="Streszczenie\x00", meeting_ref="join-abc")

    assert writer.saved == {}


def test_save_thread_note_rejects_control_characters():
    """Notatka z wątku powstaje z cudzych wiadomości — ta sama sytuacja, ta sama bramka."""
    writer = FakeNotesWriter()
    service = NotesWriteService(writer, _projects_repo())

    with pytest.raises(WriteError, match="sterując"):
        service.save_thread_note(_metadata(), body="Ustalenia\x9f", source_message_id="msg-abc")

    assert writer.saved == {}


@pytest.mark.parametrize("bialy", ["\n", "\t", "\r"], ids=["nowa-linia", "tab", "powrot-karetki"])
def test_ordinary_whitespace_is_not_mistaken_for_an_attack(bialy: str):
    """Przeciwwaga: gdyby strażnik ciął też białe znaki, akapity notatki zlepiłyby się w linię."""
    writer = FakeNotesWriter()
    service = NotesWriteService(writer, _projects_repo())

    note = service.save_note(_metadata(), body=f"pierwszy{bialy}drugi")

    assert bialy in note.body
    assert writer.saved[note.id] is note


# --- notatka z wątku „zapisz to": id deterministyczny + idempotencja (ADR 0048) ---


def test_save_thread_note_id_is_deterministic_from_source_message_id_not_title():
    # Lustro save_meeting_note: id wątku wywodzi się z source_message_id (id wzmianki), NIE ze slug
    # tytułu Claude — ten sam id wzmianki (inny tytuł) → ten sam id, inny id wzmianki → inny id.
    svc_a = NotesWriteService(FakeNotesWriter(), _projects_repo())
    svc_b = NotesWriteService(FakeNotesWriter(), _projects_repo())
    svc_c = NotesWriteService(FakeNotesWriter(), _projects_repo())

    a = svc_a.save_thread_note(_metadata(title="Przeglad"), body="t", source_message_id="msg-abc")
    b = svc_b.save_thread_note(
        _metadata(title="Zupelnie inny"), body="t", source_message_id="msg-abc"
    )
    c = svc_c.save_thread_note(_metadata(title="Przeglad"), body="t", source_message_id="msg-XYZ")

    assert a.id.startswith("mpwik/scada-integration/2025-06-12-thr-")
    assert b.id == a.id  # ten sam id wzmianki → ten sam id notatki, mimo innego tytułu
    assert c.id != a.id  # inny id wzmianki → inny id notatki


def test_thread_note_id_precheck_reflects_existing():
    writer = FakeNotesWriter()
    service = NotesWriteService(writer, _projects_repo())

    on = date(2025, 6, 12)
    assert service.thread_note_id("msg-abc", project="scada-integration", date=on) is None
    saved = service.save_thread_note(_metadata(), body="t", source_message_id="msg-abc")
    assert service.thread_note_id("msg-abc", project="scada-integration", date=on) == saved.id


def test_thread_note_id_unknown_project_returns_none():
    service = NotesWriteService(FakeNotesWriter(), _projects_repo())
    assert service.thread_note_id("msg", project="nieznany", date=date(2025, 6, 12)) is None


def test_save_thread_note_is_create_only_on_repeat(tmp_path):
    # Z REALNYM create-only writerem: ponowny zapis tej samej wzmianki rzuca NoteExistsError
    # (kolizja), zamiast tworzyć duplikat -2 — gwarancja idempotencji na poziomie FS (ADR 0048).
    from workmate.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
    from workmate.core.errors import NoteExistsError

    service = NotesWriteService(MarkdownNotesWriter(tmp_path), _projects_repo())
    service.save_thread_note(_metadata(), body="pierwsza", source_message_id="msg-abc")

    with pytest.raises(NoteExistsError):
        service.save_thread_note(_metadata(title="Inny"), body="druga", source_message_id="msg-abc")
