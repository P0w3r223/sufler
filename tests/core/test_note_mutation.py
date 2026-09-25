"""Testy bramki mutacji bazy wiedzy (ADR 0065).

Sondy pilnują przede wszystkim KOLEJNOŚCI i kierunku awarii, bo to w nich siedzi całe
bezpieczeństwo tej ścieżki: migawka przed werdyktem, twarde kontrole przed sędzią, a każda
niepewność kończy się odmową. Sędzia jest tu atrapą — jego jakość to osobna sprawa, a bramka
ma trzymać także wtedy, gdy sędzia się myli albo milczy.
"""

from __future__ import annotations

import pytest

from sufler.core.application.note_mutation import MutationRefused, NoteMutationService
from sufler.core.domain.models import Note, NoteMetadata
from sufler.core.domain.mutation import JudgeVerdict
from sufler.core.errors import WriteError

_METADATA = NoteMetadata(title="Ustalenia", project="mpwik", date="2026-08-01")

# PLIK na dysku — nie to samo, co render z modelu, i o tę różnicę tu chodzi. `status:` jest
# poza schematem ``NoteMetadata``, więc kopia składana z modelu gubiła je po cichu; sondy niżej
# porównują migawkę z TYM napisem, żeby ta strata nie mogła wrócić niezauważona.
_PLIK = "---\ntitle: Ustalenia\nproject: mpwik\nstatus: dopisane-ręcznie\n---\n\ntreść\n"


def _note(note_id: str = "biap/mpwik/2026-08-01-ustalenia", body: str = "treść") -> Note:
    return Note(id=note_id, metadata=_METADATA, body=body)


class _FakeNotes:
    def __init__(self, notes: dict[str, Note] | None = None, *, on_get=None) -> None:
        self.notes = notes or {}
        # Zaczep odpalany PRZED oddaniem notatki — odtwarza równoległy zapis, który wchodzi
        # między odczyt PLIKU (skrót + materiał migawki, jedno wywołanie) a odczyt modelu.
        self._on_get = on_get

    def all(self) -> list[Note]:
        return list(self.notes.values())

    def get(self, note_id: str) -> Note | None:
        if self._on_get is not None:
            self._on_get()
        return self.notes.get(note_id)


class _FakeWriter:
    def __init__(self) -> None:
        # (id notatki, nowa treść) — pisarz dostaje TREŚĆ, nie model: nagłówek zostaje w pliku.
        self.overwritten: list[tuple[str, str]] = []
        self.expected: list[str] = []
        self.deleted: list[str] = []
        # Skróty podane PRZY USUWANIU — kontrola wersji dotyczy obu czasowników mutacji.
        self.deleted_expected: list[str] = []
        # Znacznik wersji pliku. Podmienialny, żeby dało się odtworzyć wyścig „ktoś zmienił
        # notatkę, gdy sędzia oglądał zmianę" — między odczytem a zapisem leży wywołanie sieciowe.
        self.wersja = "wersja-v0"
        # Bajty pliku oddawane przez ``content_with_digest`` — materiał migawki.
        self.plik = _PLIK

    def exists(self, note_id: str) -> bool:
        return True

    def digest(self, note_id: str) -> str:
        return self.wersja

    def content_with_digest(self, note_id: str) -> tuple[str, str]:
        return self.plik, self.wersja

    def write(self, note: Note) -> None:
        raise AssertionError("mutacji nie wolno używać create-only `write`")

    def overwrite_body(self, note_id: str, body: str, *, expected_sha256: str) -> None:
        self.overwritten.append((note_id, body))
        self.expected.append(expected_sha256)

    def delete(self, note_id: str, *, expected_sha256: str) -> None:
        self.deleted.append(note_id)
        self.deleted_expected.append(expected_sha256)


class _FakeSnapshots:
    def __init__(self, *, fail: bool = False) -> None:
        self.saved: list[tuple[str, str]] = []
        self._fail = fail

    def save(self, note_id: str, content: str) -> str:
        if self._fail:
            raise WriteError("dysk pełny")
        # Notujemy DOKŁADNIE to, co dostał adapter — sonda ma widzieć bajty pliku, nie model.
        self.saved.append((note_id, content))
        return f"/snap/{note_id}"


class _FakeJudge:
    def __init__(self, verdict: str = "allow", *, boom: bool = False, on_review=None) -> None:
        self.verdict = verdict
        self.boom = boom
        self.seen: list = []
        # Zaczep, żeby odtworzyć to, co dzieje się PODCZAS wywołania sieciowego sędziego
        # (np. równoległa tura podmieniająca notatkę).
        self._on_review = on_review

    def review(self, request):  # noqa: ANN001, ANN201
        if self.boom:
            raise RuntimeError("sędzia padł")
        if self._on_review is not None:
            self._on_review()
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

    assert writer.overwritten[0][1] == "nowa treść"
    assert snapshots.saved == [(_note().id, _PLIK)]  # PEŁNA kopia sprzed zmiany


def test_tresc_z_wlasnym_frontmatterem_jest_odmowa_a_nie_drugim_naglowkiem():
    """Model, który notatkę wcześniej ODCZYTAŁ, oddaje ją z nagłówkiem — i to jest normalne.

    `File(read)` i `cat` (po włączeniu powłoki) podają plik W CAŁOŚCI, więc zwrócenie całości
    z powrotem do `edit` jest zachowaniem naturalnym, nie egzotycznym. Bez bramki kończyło się
    notatką z frontmatterem DWA RAZY: raz jako tekst na początku treści, raz dołożonym przez
    pisarza. Odtworzone na produkcji przy pierwszej realnej mutacji (2026-08-20) — sędzia orzekł
    `allow`, audyt `status: ok`, człowiek dostał „Zrobione ✅", a plik był uszkodzony cicho.

    Odmowa MUSI paść przed migawką i przed sędzią: uszkodzona treść nie ma być ani zapisana,
    ani skopiowana, ani oceniana.
    """
    service, writer, snapshots, judge, _l = _service()
    caly_plik = "---\ntitle: Ustalenia\nproject: mpwik\n---\n\ntreść\n\ndopisek"

    with pytest.raises(WriteError, match="SAMĄ TREŚĆ"):
        service.edit_note(_note().id, caly_plik, requester="Anna", intent="dopisek")

    assert writer.overwritten == []
    assert snapshots.saved == []
    assert judge.seen == []


def test_tresc_zaczynajaca_sie_od_poziomej_kreski_przechodzi():
    """`---` na początku bez DOMKNIĘCIA to w markdownie pozioma linia, nie nagłówek.

    Bramka szersza o ten przypadek odmawiałaby treści całkiem poprawnej — a odmowa fałszywa
    uczy operatora obchodzić bramkę, nie ufać jej.
    """
    service, writer, _s, _j, _l = _service()

    service.edit_note(_note().id, "---\n\nrozdział drugi", requester="Anna", intent="x")

    assert writer.overwritten[0][1] == "---\n\nrozdział drugi"


def test_dwie_poziome_kreski_wokol_akapitu_to_nadal_tresc_a_nie_naglowek():
    """Odmowa fałszywa była tu gorsza niż brak bramki, bo jej komunikat był NIEWYKONALNY.

    Pierwsza redakcja pytała tylko, czy DALEJ stoi druga linia `---` — a akapit obramowany
    dwiema poziomymi kreskami spełnia ten warunek, będąc treścią całkowicie poprawną. Model
    dostawał wtedy polecenie „usuń pola YAML", których w treści nie ma, więc jedynym sposobem
    na jego spełnienie było skasowanie tekstu człowieka.

    Bramka pyta więc o POLE ze schematu `NoteMetadata`, nie o kreski.
    """
    service, writer, _s, _j, _l = _service()
    akapit = "---\n\nrozdział drugi\n\n---\n\nkoniec"

    service.edit_note(_note().id, akapit, requester="Anna", intent="x")

    assert writer.overwritten[0][1] == akapit


def test_frontmatter_po_BOM_tez_jest_odmowa():
    """BOM nie jest białym znakiem dla `lstrip()` bez argumentu — a plik z Windows zaczyna się
    właśnie od niego.

    Treść przechodziła wtedy bramkę i dawała dokładnie tę korupcję, przed którą bramka stoi:
    znak niewidoczny w żadnym podglądzie decydował o tym, czy notatka wyjdzie cała, czy z
    nagłówkiem dwa razy.
    """
    service, writer, snapshots, judge, _l = _service()
    z_bom = "\ufeff---\ntitle: Ustalenia\nproject: mpwik\n---\n\ntreść"

    with pytest.raises(WriteError, match="SAMĄ TREŚĆ"):
        service.edit_note(_note().id, z_bom, requester="Anna", intent="x")

    assert writer.overwritten == []
    assert snapshots.saved == []
    assert judge.seen == []


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

    assert writer.overwritten[0][1] == "nowa"
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


def test_confirmation_rests_on_the_turn_differing_within_one_conversation():
    """Zgoda wynika z RÓŻNICY tur — sprawdzane w obrębie JEDNEJ rozmowy.

    Historia tej sondy jest pouczająca. Najpierw nazywała się
    „…does_not_transfer_between_conversations…", a jej asercje pokazywały coś odwrotnego; nazwę
    poprawiono wtedy do treści, zamiast sprawdzić treść wobec decyzji. ADR 0065 §6 mówi jednak
    wprost: to „human checkpoint **in the Teams thread**", a dowodem ma być, że człowiek odezwał
    się PO ZOBACZENIU, co się zmieni. Zobaczenie dzieje się w konkretnym wątku, więc granicą jest
    para (rozmowa, tura), nie sama tura. Sonda niżej pilnuje drugiej połowy tej pary.
    """
    service, writer, _s, _j, _l = _service(judge=_FakeJudge("confirm"), allow_delete=True)

    with pytest.raises(MutationRefused):
        service.delete_note(
            _note().id, requester="Anna", intent="x", turn_token="tura-1", conversation="watek-A"
        )

    service.delete_note(
        _note().id, requester="Anna", intent="x", turn_token="tura-2", conversation="watek-A"
    )

    assert writer.deleted == [_note().id]


def test_confirmation_does_not_transfer_between_conversations():
    """Zapowiedź z wątku A nie autoryzuje wykonania w wątku B.

    Rejestr zapowiedzi powstaje RAZ na proces, a token tury jest świeży co turę — bez zakresu
    rozmowy w kluczu wystarczyło, że Anna poprosi o tę samą zmianę gdzie indziej w ciągu TTL, by
    weszła bez zapowiedzi w tym miejscu. Człowiek widział wtedy zapowiedź w innym wątku albo
    wcale, czyli punkt kontrolny nie miał materiału, który ma sprawdzać (ADR 0065 §6).
    """
    service, writer, _s, _j, _l = _service(judge=_FakeJudge("confirm"), allow_delete=True)

    with pytest.raises(MutationRefused):
        service.delete_note(
            _note().id, requester="Anna", intent="x", turn_token="tura-1", conversation="watek-A"
        )
    with pytest.raises(MutationRefused):
        service.delete_note(
            _note().id, requester="Anna", intent="x", turn_token="tura-2", conversation="watek-B"
        )

    assert writer.deleted == []


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
    assert snapshots.saved == [(_note().id, _PLIK)]
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


# --- Kontrola wersji pliku: znacznik brany PRZED sędzią (wyścig przez wywołanie sieciowe) ---


def test_the_file_version_is_captured_before_the_judge_is_asked():
    """Między odczytem a zapisem leży wywołanie SIECIOWE, a drzwi obsługują tury równolegle.

    Gdyby znacznik wersji powstawał po werdykcie, zmiana wprowadzona przez inną turę w trakcie
    oceny zostałaby po cichu nadpisana — kontrola wersji porównywałaby plik z samym sobą.
    Sonda podmienia wersję DOKŁADNIE w chwili, gdy sędzia „jest w sieci", i sprawdza, że do
    ``overwrite`` idzie znacznik SPRZED oceny (realny writer odbije taki zapis).
    """
    writer = _FakeWriter()
    judge = _FakeJudge(on_review=lambda: setattr(writer, "wersja", "wersja-v1-od-kogos-innego"))
    service, _w, _s, _j, _l = _service(writer=writer, judge=judge)

    service.edit_note(_note().id, "nowa treść", requester="Anna", intent="x")

    assert writer.expected == ["wersja-v0"]


def test_the_captured_version_is_passed_to_the_writer_not_recomputed():
    service, writer, _s, _j, _l = _service()

    service.edit_note(_note().id, "nowa treść", requester="Anna", intent="x")

    assert writer.expected == [writer.wersja]


@pytest.mark.parametrize("operacja", ["edit", "delete"])
def test_the_version_marker_is_taken_BEFORE_the_content_the_snapshot_is_made_of(operacja: str):
    """Skrót i materiał migawki idą z JEDNEGO odczytu — a zapis w oknie ma odbić się o wersję.

    Historycznie były to dwa osobne odczyty i kolejność rozstrzygała, co ginie: przy „treść,
    potem skrót" zapis wchodzący między nie dawał skrót NOWEJ wersji i migawkę STAREJ, więc
    kontrola wersji przepuszczała operację, a wersja pośrednia znikała bez kopii. Od
    ``content_with_digest`` okna nie ma — obie wartości pochodzą z tych samych bajtów.

    Sonda pilnuje tego, co zostało: równoległy zapis wchodzący między odczyt PLIKU a odczyt
    modelu nie może podmienić znacznika idącego do writera. Do zapisu ma iść wersja SPRZED
    podmiany, żeby realny writer operację odbił i nic nie zginęło.
    """
    writer = _FakeWriter()
    notes = _FakeNotes(
        {_note().id: _note()},
        on_get=lambda: setattr(writer, "wersja", "wersja-v1-od-kogos-innego"),
    )
    service, _w, _s, _j, _l = _service(notes=notes, writer=writer, allow_delete=True)

    if operacja == "edit":
        service.edit_note(_note().id, "nowa treść", requester="Anna", intent="x")
        podane = writer.expected
    else:
        service.delete_note(_note().id, requester="Anna", intent="x")
        podane = writer.deleted_expected

    assert podane == ["wersja-v0"], (
        "skrót ma pochodzić SPRZED odczytu treści — inaczej migawka jest starsza niż skrót "
        "i zapis w oknie przechodzi kontrolę wersji"
    )


def test_DELETE_takes_the_file_version_right_after_the_read_just_like_edit():
    """Usunięcie dzieli odczyt od zapisu dokładnie tym samym wywołaniem sieciowym co edycja.

    Bez kontroli wersji równoległa edycja z okna oczekiwania na sędziego (a przy werdykcie
    „confirm" — z całej tury) znikała BEZ MIGAWKI: migawka zabezpiecza wersję, którą bramka
    PRZECZYTAŁA, więc wersja pośrednia nie miała żadnej kopii. Port ``NotesWriter.overwrite``
    nazywa ten stan jedynym, którego ta warstwa ma nie dopuszczać.
    """
    writer = _FakeWriter()
    judge = _FakeJudge(on_review=lambda: setattr(writer, "wersja", "wersja-v1-od-kogos-innego"))
    service, _w, _s, _j, _l = _service(writer=writer, judge=judge, allow_delete=True)

    service.delete_note(_note().id, requester="Anna", intent="x")

    assert writer.deleted == [_note().id]
    assert writer.deleted_expected == ["wersja-v0"], (
        "do usunięcia idzie znacznik SPRZED oceny — realny writer odbije zapis na innej wersji"
    )


# --- ADR 0066: pochodzenie tury dojeżdża do sędziego -------------------------------


def test_the_judge_sees_the_trust_class_and_the_taint_of_the_turn():
    """Sędzia ma wiedzieć, CZYJA to prośba w sensie pochodzenia, nie tylko czyim nazwiskiem
    podpisana: skażona rozmowa (przeczytany plik, wynik narzędzia) eskaluje ocenę."""
    service, _w, _s, judge, _l = _service()

    service.edit_note(
        _note().id, "nowa", requester="Anna", intent="x", trust_class="T2", tainted=True
    )

    (request,) = judge.seen
    assert (request.trust_class, request.tainted) == ("T2", True)


def test_a_caller_that_says_nothing_about_origin_is_treated_as_the_worst_case():
    """Domysł ściśle BEZPIECZNY: brak informacji o pochodzeniu = „skażona, nieznana klasa".

    Odwrotny domysł („nic nie podano, więc czysto") czyniłby z niewiedzy automatyczne złagodzenie
    oceny — dokładnie tam, gdzie 0066 dopiero się wpina i wołający jeszcze nic nie podają.
    """
    service, _w, _s, judge, _l = _service()

    service.edit_note(_note().id, "nowa", requester="Anna", intent="x")

    (request,) = judge.seen
    assert (request.trust_class, request.tainted) == ("unknown", True)


def test_delete_carries_the_origin_of_the_turn_too():
    service, _w, _s, judge, _l = _service(allow_delete=True)

    service.delete_note(_note().id, requester="Anna", intent="x", trust_class="T1", tainted=False)

    (request,) = judge.seen
    assert (request.kind, request.trust_class, request.tainted) == ("delete", "T1", False)


# --- Rozpoznawanie identyfikatorów DETERMINISTYCZNYCH (po dacie, nie w całym id) ----


@pytest.mark.parametrize(
    "note_id",
    [
        "biap/mpwik/2026-08-01-mtg-abc",
        "biap/mpwik/2026-08-01-thr-abc",
    ],
    ids=["spotkanie", "watek"],
)
def test_deterministic_ids_are_read_only_for_delete_as_well(note_id: str):
    """Bramka niezmienności obowiązuje OBIE drogi — inaczej kasowanie byłoby obejściem edycji."""
    nota = _note(note_id)
    service, writer, snapshots, judge, _l = _service(
        notes=_FakeNotes({note_id: nota}), allow_delete=True
    )

    with pytest.raises(WriteError, match="tylko do odczytu"):
        service.delete_note(note_id, requester="Anna", intent="x")

    assert writer.deleted == [] and snapshots.saved == [] and judge.seen == []


@pytest.mark.parametrize(
    "note_id",
    [
        "biap/mpwik/2026-08-01-zwykle-ustalenia",
        "biap/mpwik/2026-08-01-podsumowanie-mtg",  # znacznik bez domykającego myślnika
        "biap/mpwik/2026-08-01-mtgowe-porzadki",  # znacznik bez otwierającego myślnika
    ],
    ids=["bez-znacznika", "mtg-na-koncu-slugu", "mtg-zrosniete-ze-slowem"],
)
def test_an_ordinary_note_stays_mutable(note_id: str):
    """Zwykła notatka użytkownika jest ZMIENIALNA — bramka niezmienności nie może jej dotyczyć."""
    service, writer, _s, _j, _l = _service(notes=_FakeNotes({note_id: _note(note_id)}))

    service.edit_note(note_id, "nowa treść", requester="Anna", intent="poprawka")

    assert writer.overwritten[0][1] == "nowa treść"


def test_the_date_prefix_is_what_the_marker_is_measured_against():
    """Znacznik liczy się dopiero PO dziesiątym znaku nazwy — to cała rola ``_DATE_PREFIX_LEN``."""
    note_id = "biap/mpwik/-mtg-bc-01-zwykla"  # znacznik mieści się w prefiksie „daty"
    service, writer, _s, _j, _l = _service(notes=_FakeNotes({note_id: _note(note_id)}))

    service.edit_note(note_id, "nowa", requester="Anna", intent="x")

    assert writer.overwritten[0][1] == "nowa"


@pytest.mark.parametrize(
    "note_id",
    [
        "biap/mpwik/2026-08-01-ustalenia-mtg-tygodniowy",
        "biap/mpwik/2026-08-01-notatka-mtg-z-klientem",
        "biap/mpwik/2026-08-01-plan-thr-owy",
    ],
    ids=["ustalenia-mtg-tygodniowy", "notatka-mtg-z-klientem", "plan-thr-owy"],
)
def test_a_users_title_containing_the_marker_does_NOT_freeze_the_note(note_id: str):
    """Znacznik liczy się jako PREFIKS ogona, nie jako podłańcuch — inaczej tytuł zamraża notatkę.

    Docstring ``_jest_deterministyczna`` obiecuje, że „tytuł użytkownika nie może przypadkiem
    uczynić notatki niezmienną", a ``_DATE_PREFIX_LEN`` pilnował tylko tego, żeby znacznik nie
    trafił się w samej DACIE — a data nigdy nie zawiera liter. Test podłańcucha na całym ogonie
    zamrażał więc KAŻDY slug ze środkiem ``-mtg-``/``-thr-``, na zawsze.

    Te identyfikatory nie są wymyślone: ``NotesWriteService.save_note`` składa je ze slugu
    tytułu, więc notatka „Ustalenia mtg tygodniowy" dostawała dokładnie taki id — i odmowę
    z komunikatem, który dodatkowo kłamał o jej pochodzeniu („pochodzi ze spotkania lub wątku").
    Prawdziwe znaczniki stoją ZARAZ za datą (``2026-08-01-mtg-<skrót>``, ``paths``).
    """
    service, writer, _s, judge, _l = _service(notes=_FakeNotes({note_id: _note(note_id)}))

    service.edit_note(note_id, "nowa treść", requester="Anna", intent="poprawka")

    assert writer.overwritten[0][1] == "nowa treść"
    assert judge.seen, "zwykła notatka ma dojść do sędziego, a nie odbić się o niezmienność"


# --- Kształt odmowy: wyjątek, który wołający potrafi pokazać ----------------------


def test_refusal_is_a_write_error_so_existing_envelopes_catch_it():
    """Dziedziczenie z ``WriteError`` jest kontraktem: każda istniejąca koperta błędów zapisu
    łapie odmowę bez zmian i żaden wołający nie zamienia jej w traceback."""
    service, _w, _s, _j, _l = _service(judge=_FakeJudge("refuse"))

    with pytest.raises(WriteError):
        service.edit_note(_note().id, "nowa", requester="Anna", intent="x")


def test_refusal_carries_the_verdict_and_the_snapshot_location():
    """Wołający (narzędzie ``File``) przepisuje werdykt na wynik dla modelu — musi go dostać."""
    service, _w, _s, _j, _l = _service(judge=_FakeJudge("refuse"))

    with pytest.raises(MutationRefused) as exc:
        service.edit_note(_note().id, "nowa", requester="Anna", intent="x")

    assert exc.value.outcome.applied is False
    assert exc.value.outcome.verdict.verdict == "refuse"
    assert str(exc.value) == exc.value.outcome.verdict.reason


def test_a_snapshot_is_taken_even_when_the_judge_refuses():
    """Migawka PRZED werdyktem, choć przy odmowie okaże się niepotrzebna — kolejność odwrotna
    zostawiałaby operację zatwierdzoną i niezabezpieczoną. Kopia jest tania, utrata notatki nie."""
    service, _w, snapshots, _j, _l = _service(judge=_FakeJudge("refuse"))

    with pytest.raises(MutationRefused) as exc:
        service.edit_note(_note().id, "nowa", requester="Anna", intent="x")

    assert snapshots.saved == [(_note().id, _PLIK)]
    assert exc.value.outcome.snapshot.endswith(_note().id)


def test_a_failed_snapshot_leaves_no_location_to_point_at():
    service, _w, _s, _j, _l = _service(snapshots=_FakeSnapshots(fail=True))

    with pytest.raises(MutationRefused) as exc:
        service.edit_note(_note().id, "nowa", requester="Anna", intent="x")

    assert exc.value.outcome.snapshot == ""


# --- Pozostałe brzegi ------------------------------------------------------------


def test_deleting_a_missing_note_is_refused_before_a_snapshot_is_taken():
    service, writer, snapshots, judge, _l = _service(notes=_FakeNotes({}), allow_delete=True)

    with pytest.raises(WriteError, match="nie istnieje"):
        service.delete_note("nie/ma/takiej", requester="Anna", intent="x")

    assert writer.deleted == [] and snapshots.saved == [] and judge.seen == []


def test_the_new_body_is_stripped_before_it_is_judged_and_written():
    """Ten sam kształt treści widzi sędzia, klucz zapowiedzi i plik — inaczej powtórzenie prośby
    z inną liczbą spacji liczyłoby się jako INNA zmiana i gubiło potwierdzenie."""
    service, writer, _s, judge, _l = _service()

    service.edit_note(_note().id, "\n\n  nowa treść  \n", requester="Anna", intent="x")

    (request,) = judge.seen
    assert request.new_body == "nowa treść"
    assert writer.overwritten[0][1] == "nowa treść"


def test_whitespace_only_difference_reuses_the_same_confirmation():
    """Konsekwencja normalizacji: zapowiedź złożona z dodatkowymi spacjami domyka się przy
    powtórzeniu bez nich — to TA SAMA zmiana, nie nowa."""
    service, writer, _s, _j, _l = _service(judge=_FakeJudge("confirm"))

    with pytest.raises(MutationRefused):
        service.edit_note(
            _note().id, "  nowa treść  ", requester="Anna", intent="x", turn_token="1"
        )
    service.edit_note(_note().id, "nowa treść", requester="Anna", intent="x", turn_token="2")

    assert writer.overwritten[0][1] == "nowa treść"


def test_metadata_is_untouched_by_an_edit():
    """Metadane są poza zasięgiem rozmyślnie: z nich wywodzi się identyfikator i miejsce pliku,
    więc ich zmiana byłaby PRZENIESIENIEM notatki, nie poprawką treści.

    Gwarancja jest teraz STRUKTURALNA, nie umowna: do pisarza idzie identyfikator i sama treść,
    więc nie ma czym nadpisać nagłówka. Poprzednia redakcja przekazywała cały model i pilnowała
    umową, że metadane są te same — a mimo zgodnego modelu plik i tak tracił przy każdej edycji
    komentarze YAML oraz pola spoza schematu, bo nagłówek składał się z modelu na nowo. Bajtowa
    nietykalność nagłówka ma własną sondę przy adapterze (``test_note_mutation_adapters``).
    """
    service, writer, _s, _j, _l = _service()

    service.edit_note(_note().id, "nowa treść", requester="Anna", intent="x")

    assert writer.overwritten == [(_note().id, "nowa treść")]


def test_an_allowed_delete_returns_the_verdict_along_with_the_copy():
    service, _w, _s, _j, _l = _service(allow_delete=True)

    wynik = service.delete_note(_note().id, requester="Anna", intent="duplikat")

    assert wynik.applied is True
    assert wynik.verdict.verdict == "allow"


def test_the_gate_never_uses_the_create_only_write_path():
    """``_FakeWriter.write`` wysadza test, jeśli mutacja kiedykolwiek sięgnie po create-only
    ``write``: to droga TWORZENIA nowej notatki (CLAUDE.md #2), a nie zmiany istniejącej."""
    service, writer, _s, _j, _l = _service(allow_delete=True)

    service.edit_note(_note().id, "nowa", requester="Anna", intent="x")
    service.delete_note(_note().id, requester="Anna", intent="x")

    assert len(writer.overwritten) == 1 and writer.deleted == [_note().id]


# --- Ujście werdyktu do audytu (ADR 0065 §8) -------------------------------------------


class _WriterKtoryPadaPoZgodzie(_FakeWriter):
    """Pisarz odbijający zapis kontrolą wersji — równoległa tura weszła w okno po orzeczeniu."""

    def overwrite_body(self, note_id: str, body: str, *, expected_sha256: str) -> None:
        raise WriteError("notatka zmieniła się od odczytu")


def test_the_verdict_is_reported_when_it_is_made_not_when_the_write_succeeds():
    """Sonda właściwego MOMENTU zgłoszenia.

    Sędzia orzekł `allow`, migawka powstała, a zapis padł na kontroli wersji — okno, które
    ``_require_mutable`` opisuje jako realne. Zgłoszenie po udanym zapisie gubiłoby ten werdykt
    i zostawiało wiersz audytu nieodróżnialny od „sędzia w ogóle nie biegł".
    """
    zgloszone: list[tuple[str, str]] = []
    service, _w, _s, _j, _l = _service(writer=_WriterKtoryPadaPoZgodzie())

    with pytest.raises(WriteError):
        service.edit_note(
            _note().id,
            "nowa treść",
            requester="Anna",
            intent="x",
            verdict_sink=lambda v, r: zgloszone.append((v, r)),
        )

    assert zgloszone == [("allow", "powód")]


def test_a_failed_snapshot_reports_no_verdict():
    """Odmowa TECHNICZNA — nikt nie orzekał. Wiersz „refuse" z tekstem wyjątku udawałby
    orzeczenie, i to akurat wtedy, gdy dziennik ma wyjaśnić awarię infrastruktury."""
    zgloszone: list[tuple[str, str]] = []
    service, _w, _s, _j, _l = _service(snapshots=_FakeSnapshots(fail=True))

    with pytest.raises(WriteError):
        service.edit_note(
            _note().id,
            "nowa treść",
            requester="Anna",
            intent="x",
            verdict_sink=lambda v, r: zgloszone.append((v, r)),
        )

    assert zgloszone == []


def test_an_unavailable_judge_reports_no_verdict():
    """Ta sama zasada co wyżej: sędzia, który nie odpowiedział, niczego nie orzekł."""
    zgloszone: list[tuple[str, str]] = []
    service, _w, _s, _j, _l = _service(judge=_FakeJudge(boom=True))

    with pytest.raises(WriteError):
        service.edit_note(
            _note().id,
            "nowa treść",
            requester="Anna",
            intent="x",
            verdict_sink=lambda v, r: zgloszone.append((v, r)),
        )

    assert zgloszone == []


def test_a_delete_reports_its_verdict_too():
    zgloszone: list[tuple[str, str]] = []
    service, _w, _s, _j, _l = _service(allow_delete=True)

    service.delete_note(
        _note().id,
        requester="Anna",
        intent="duplikat",
        verdict_sink=lambda v, r: zgloszone.append((v, r)),
    )

    assert zgloszone == [("allow", "powód")]
