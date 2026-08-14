"""Testy bramki mutacji bazy wiedzy (ADR 0065).

Sondy pilnują przede wszystkim KOLEJNOŚCI i kierunku awarii, bo to w nich siedzi całe
bezpieczeństwo tej ścieżki: migawka przed werdyktem, twarde kontrole przed sędzią, a każda
niepewność kończy się odmową. Sędzia jest tu atrapą — jego jakość to osobna sprawa, a bramka
ma trzymać także wtedy, gdy sędzia się myli albo milczy.
"""

from __future__ import annotations

import pytest

from workmate.core.application.note_mutation import MutationRefused, NoteMutationService
from workmate.core.domain.models import Note, NoteMetadata
from workmate.core.domain.mutation import JudgeVerdict
from workmate.core.errors import WriteError

_METADATA = NoteMetadata(title="Ustalenia", project="mpwik", date="2026-08-01")


def _note(note_id: str = "biap/mpwik/2026-08-01-ustalenia", body: str = "treść") -> Note:
    return Note(id=note_id, metadata=_METADATA, body=body)


class _FakeNotes:
    def __init__(self, notes: dict[str, Note] | None = None) -> None:
        self.notes = notes or {}

    def all(self) -> list[Note]:
        return list(self.notes.values())

    def get(self, note_id: str) -> Note | None:
        return self.notes.get(note_id)


class _FakeWriter:
    def __init__(self) -> None:
        self.overwritten: list[Note] = []
        self.expected: list[str] = []
        self.deleted: list[str] = []

    def exists(self, note_id: str) -> bool:
        return True

    def digest(self, note_id: str) -> str:
        return "wersja-v0"

    def write(self, note: Note) -> None:
        raise AssertionError("mutacji nie wolno używać create-only `write`")

    def overwrite(self, note: Note, *, expected_sha256: str) -> None:
        self.overwritten.append(note)
        self.expected.append(expected_sha256)

    def delete(self, note_id: str) -> None:
        self.deleted.append(note_id)


class _FakeSnapshots:
    def __init__(self, *, fail: bool = False) -> None:
        self.saved: list[tuple[str, str, str]] = []
        self._fail = fail

    def save(self, note: Note) -> str:
        if self._fail:
            raise WriteError("dysk pełny")
        # Notujemy metadane RAZEM z treścią — sonda ma widzieć, że kopia jest pełna.
        self.saved.append((note.id, note.body, note.metadata.title))
        return f"/snap/{note.id}"


class _FakeJudge:
    def __init__(self, verdict: str = "allow", *, boom: bool = False) -> None:
        self.verdict = verdict
        self.boom = boom
        self.seen: list = []

    def review(self, request):  # noqa: ANN001, ANN201
        if self.boom:
            raise RuntimeError("sędzia padł")
        self.seen.append(request)
        return JudgeVerdict(self.verdict, "powód")  # type: ignore[arg-type]


class _FakeLedger:
    def __init__(self) -> None:
        self.keys: dict[str, str] = {}

    def turn_of(self, key: str) -> str | None:
        return self.keys.get(key)

    def remember(self, key: str, turn_token: str) -> None:
        self.keys[key] = turn_token

    def forget(self, key: str) -> None:
        self.keys.pop(key, None)


def _service(**kwargs):
    notes = kwargs.pop("notes", None) or _FakeNotes({_note().id: _note()})
    writer = kwargs.pop("writer", None) or _FakeWriter()
    snapshots = kwargs.pop("snapshots", None) or _FakeSnapshots()
    judge = kwargs.pop("judge", None) or _FakeJudge()
    ledger = kwargs.pop("ledger", None) or _FakeLedger()
    service = NoteMutationService(notes, writer, snapshots, judge, ledger, **kwargs)
    return service, writer, snapshots, judge, ledger


def test_allowed_edit_replaces_the_body_and_keeps_a_snapshot():
    service, writer, snapshots, _judge, _l = _service()

    service.edit_note(_note().id, "nowa treść", requester="Anna", intent="poprawka literówki")

    assert writer.overwritten[0].body == "nowa treść"
    assert snapshots.saved == [(_note().id, "treść", "Ustalenia")]  # PEŁNA kopia sprzed zmiany


def test_snapshot_failure_refuses_the_mutation():
    """Migawka jest jedyną odwracalnością, jaka została po zniesieniu create-only.

    „Nie udało się zrobić kopii, więc zmieniłem mimo to" byłoby dokładną odwrotnością tego,
    po co ta warstwa istnieje.
    """
    service, writer, _s, judge, _l = _service(snapshots=_FakeSnapshots(fail=True))

    with pytest.raises(MutationRefused, match="kopii"):
        service.edit_note(_note().id, "nowa", requester="Anna", intent="x")

    assert writer.overwritten == []
    assert judge.seen == []  # sędzia nawet nie pytany — kolejność kontroli trzyma


def test_judge_failure_refuses_instead_of_passing():
    """Awaria sieci nie może stać się automatyczną zgodą na zmianę bazy wiedzy."""
    service, writer, _s, _j, _l = _service(judge=_FakeJudge(boom=True))

    with pytest.raises(MutationRefused, match="sędzia"):
        service.edit_note(_note().id, "nowa", requester="Anna", intent="x")

    assert writer.overwritten == []


def test_refused_verdict_keeps_the_note_intact():
    service, writer, _s, _j, _l = _service(judge=_FakeJudge("refuse"))

    with pytest.raises(MutationRefused):
        service.edit_note(_note().id, "nowa", requester="Anna", intent="x")

    assert writer.overwritten == []


def test_confirm_is_announced_first_and_applied_only_from_a_later_turn():
    """Punkt kontrolny człowieka: zapowiedź w jednej turze, wykonanie w NASTĘPNEJ.

    Tura powstaje tylko wtedy, gdy ktoś napisał, więc powrót prośby z innej tury dowodzi, że
    człowiek odezwał się po zobaczeniu, co miałoby się zmienić.
    """
    service, writer, _s, _j, ledger = _service(judge=_FakeJudge("confirm"))

    with pytest.raises(MutationRefused):
        service.edit_note(_note().id, "nowa", requester="Anna", intent="x", turn_token="tura-1")
    assert writer.overwritten == []
    assert ledger.keys  # zapowiedź zapamiętana

    service.edit_note(_note().id, "nowa", requester="Anna", intent="x", turn_token="tura-2")

    assert writer.overwritten[0].body == "nowa"
    assert not ledger.keys  # zgoda JEDNORAZOWA — kolejna zmiana zaczyna od zapowiedzi


def test_model_cannot_confirm_itself_within_one_turn():
    """REGRESJA (znalezisko krytyczne): pętla narzędzi ma do ośmiu rund w JEDNEJ turze, a każda
    runda może nieść wiele wywołań naraz.

    Dopóki zgoda wynikała z samego „ta prośba już kiedyś padła", model zapowiadał i wykonywał
    kasowanie sam, bez udziału człowieka — a warstwa, która miała być ostatnią przed
    nieodwracalnością, nie kosztowała go nic poza powtórzeniem wywołania.
    """
    service, writer, _s, _j, _l = _service(judge=_FakeJudge("confirm"), allow_delete=True)

    for _ in range(5):
        with pytest.raises(MutationRefused):
            service.delete_note(_note().id, requester="Anna", intent="x", turn_token="ta-sama")

    assert writer.deleted == []


def test_confirmation_does_not_transfer_between_conversations_of_one_person():
    """Zapowiedź z kanału nie domyka się w rozmowie prywatnej — token tury jest inny."""
    service, writer, _s, _j, _l = _service(judge=_FakeJudge("confirm"), allow_delete=True)

    with pytest.raises(MutationRefused):
        service.delete_note(_note().id, requester="Anna", intent="x", turn_token="kanal-1")

    # Inna tura, więc zgoda przechodzi — i to jest zamierzone: dowodem jest odezwanie się
    # człowieka, nie miejsce, w którym to zrobił. Sonda pilnuje, że mechanizm opiera się na
    # RÓŻNICY tur, a nie na przypadkowej zbieżności kluczy.
    service.delete_note(_note().id, requester="Anna", intent="x", turn_token="kanal-2")

    assert writer.deleted == [_note().id]


def test_confirmation_does_not_transfer_to_a_different_change():
    """Zapowiedź „popraw akapit o terminie" nie autoryzuje podmiany całej notatki."""
    service, writer, _s, _j, _l = _service(judge=_FakeJudge("confirm"))

    with pytest.raises(MutationRefused):
        service.edit_note(
            _note().id, "drobna poprawka", requester="Anna", intent="x", turn_token="tura-1"
        )
    with pytest.raises(MutationRefused):
        service.edit_note(
            _note().id, "CAŁKIEM INNA TREŚĆ", requester="Anna", intent="x", turn_token="tura-2"
        )

    assert writer.overwritten == []


def test_confirmation_does_not_transfer_between_people():
    service, writer, _s, _j, _l = _service(judge=_FakeJudge("confirm"))

    with pytest.raises(MutationRefused):
        service.edit_note(_note().id, "nowa", requester="Anna", intent="x", turn_token="tura-1")
    with pytest.raises(MutationRefused):
        service.edit_note(_note().id, "nowa", requester="Piotr", intent="x", turn_token="tura-2")

    assert writer.overwritten == []


def test_meeting_and_thread_notes_stay_read_only():
    """Ich niezmienność to MECHANIZM idempotencji (ADR 0043/0048), nie ostrożność.

    Powtórne przetworzenie tego samego spotkania ma trafić na istniejący plik i odbić się —
    mutacja rozbroiłaby tę gwarancję i drugi przebieg nadpisałby wynik pierwszego.
    """
    mtg = _note("biap/mpwik/2026-08-01-mtg-abc")
    service, writer, _s, judge, _l = _service(notes=_FakeNotes({mtg.id: mtg}))

    with pytest.raises(WriteError, match="tylko do odczytu"):
        service.edit_note(mtg.id, "nowa", requester="Anna", intent="x")

    assert writer.overwritten == [] and judge.seen == []


def test_missing_note_is_refused_before_anything_else():
    service, _w, snapshots, judge, _l = _service(notes=_FakeNotes({}))

    with pytest.raises(WriteError, match="nie istnieje"):
        service.edit_note("nie/ma/takiej", "nowa", requester="Anna", intent="x")

    assert snapshots.saved == [] and judge.seen == []


def test_delete_is_off_unless_explicitly_enabled():
    """Kasowanie ma WŁASNĄ bramkę, bo ADR wiąże je z działającą kopią zapasową."""
    service, writer, _s, _j, _l = _service()

    with pytest.raises(WriteError, match="wyłączone"):
        service.delete_note(_note().id, requester="Anna", intent="x")

    assert writer.deleted == []


def test_enabled_delete_removes_the_note_after_a_snapshot():
    service, writer, snapshots, _j, _l = _service(allow_delete=True)

    wynik = service.delete_note(_note().id, requester="Anna", intent="duplikat")

    assert writer.deleted == [_note().id]
    assert snapshots.saved == [(_note().id, "treść", "Ustalenia")]
    assert wynik.snapshot.endswith(_note().id)


def test_dangerous_content_is_rejected_before_the_judge():
    """Ten sam strażnik, co przy tworzeniu notatki — mutacja nie jest tylną furtką na bajty
    sterujące w bazie wiedzy."""
    service, _w, _s, judge, _l = _service()

    with pytest.raises(WriteError):
        service.edit_note(_note().id, "zła\x00treść", requester="Anna", intent="x")

    assert judge.seen == []


def test_judge_sees_the_requester_and_both_versions():
    """Sędzia ma orzekać na komplecie faktów, nie na samej deklaracji modelu."""
    service, _w, _s, judge, _l = _service()

    service.edit_note(_note().id, "nowa treść", requester="Anna", intent="poprawka")

    (request,) = judge.seen
    assert request.requester == "Anna"
    assert request.current_body == "treść" and request.new_body == "nowa treść"
    assert request.intent == "poprawka"
