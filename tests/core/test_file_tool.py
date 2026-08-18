"""Testy narzędzia ``File`` (ADR 0064) — materializacja pliku rozmowy do kontekstu modelu.

Sedno tego narzędzia nie jest w tym, ŻE czyta plik (to potrafi powłoka), tylko w tym, ŻE
wstawia go do kontekstu jako blok obrazu/dokumentu — czego powłoka nie potrafi z definicji.
Dlatego sondy pilnują przede wszystkim DROGI pliku: że bajty NIE wracają w wyniku narzędzia
(``tool_result`` nie unosi bloku ``document`` i bywa czyszczony), tylko osobną kolejką, oraz
że budżet i odmowy są rozróżnialne dla modelu.

Powierzchnia MCP jest zamrożona osobnym golden-testem — ``File`` jest agent-only, więc tam
się nie pojawia (sonda negatywna niżej).
"""

from __future__ import annotations

import pytest

from workmate.core.application.tools import build_file_catalog
from workmate.core.application.workspace import WorkspaceService
from workmate.core.domain.workspace import WorkspaceFile, WorkspaceScope
from workmate.core.ports.llm import Attachment, AttachmentQueue
from workmate.core.ports.materialization import MaterializationLimits

_SCOPE = WorkspaceScope("teams_graph", "team/chan/root")
_PDF = b"%PDF-1.7 udawany dokument"


class _FakeWorkspace:
    """Repozytorium katalogu roboczego w pamięci — oddaje bajty tak, jak dysk."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files or {}

    def list(self, scope_dir: str) -> list[WorkspaceFile]:
        return [
            WorkspaceFile(rp.rsplit("/", 1)[-1], rp, len(data))
            for rp, data in self.files.items()
            if rp.startswith(f"{scope_dir}/")
        ]

    def read(self, scope_dir: str, name: str) -> str | None:
        data = self.files.get(f"{scope_dir}/{name}")
        return data.decode("utf-8", errors="replace") if data is not None else None

    def read_bytes(self, scope_dir: str, name: str) -> bytes | None:
        return self.files.get(f"{scope_dir}/{name}")


class _FakeMaterializer:
    """Port materializacji: PDF → dokument, ``.txt`` → tekst, reszta → None (nieobsługiwany)."""

    def materialize(self, name: str, data: bytes) -> tuple[Attachment, int] | None:
        if name.endswith(".pdf"):
            return Attachment("document", "application/pdf", name, data_base64="AAAA"), len(data)
        if name.endswith(".txt"):
            # Tekst nie niesie bajtów do API — stąd 0, jak w materializerze drzwi.
            return Attachment("text", "text/plain", name, text=data.decode("utf-8")), 0
        return None


def _tool(
    *,
    files: dict[str, bytes] | None = None,
    budget: int = 1_000_000,
    materializer: object | None = None,
    limits: MaterializationLimits | None = None,
):
    scope_dir = str(_SCOPE.dirpath())
    repo = _FakeWorkspace({f"{scope_dir}/{n}": d for n, d in (files or {}).items()})
    queue = AttachmentQueue(budget_bytes=budget)
    (spec,) = build_file_catalog(
        _SCOPE,
        WorkspaceService(repo),
        materializer or _FakeMaterializer(),  # type: ignore[arg-type]
        queue,
        limits or MaterializationLimits(max_bytes=10_000_000, max_extract_bytes=10_000_000),
    )
    return spec, queue


def test_read_puts_the_file_in_the_queue_not_in_the_tool_result():
    """Bajty jadą kolejką, wynik niesie samo potwierdzenie — to cała istota szwu ADR 0064."""
    spec, queue = _tool(files={"umowa.pdf": _PDF})

    result = spec.fn(action="read", name="umowa.pdf")

    assert result["materialized"] is True
    assert result["kind"] == "document"
    assert "AAAA" not in str(result)  # base64 NIE wraca wynikiem narzędzia
    (attachment,) = queue.drain()
    assert (attachment.kind, attachment.name) == ("document", "umowa.pdf")


def test_drain_empties_the_queue_so_a_file_is_not_sent_twice():
    spec, queue = _tool(files={"umowa.pdf": _PDF})
    spec.fn(action="read", name="umowa.pdf")

    assert len(queue.drain()) == 1
    assert queue.drain() == ()


def test_missing_file_is_a_recoverable_error_not_an_exception():
    spec, queue = _tool(files={})

    result = spec.fn(action="read", name="nie-ma.pdf")

    assert "nie istnieje" in result["error"]
    assert queue.drain() == ()


def test_unsupported_format_points_at_the_shell_extractor():
    """Odmowa ma prowadzić do wyjścia: tekst z dokumentu model wyciągnie powłoką."""
    spec, _ = _tool(files={"dane.nieznany": b"cokolwiek"})

    result = spec.fn(action="read", name="dane.nieznany")

    assert "nieobsługiwany format" in result["error"]
    assert "workmate-extract" in result["error"]


def test_exhausted_budget_refuses_with_the_remaining_amount():
    """Model musi wiedzieć, ILE zostało — inaczej ponawia to samo pobranie w kółko."""
    spec, queue = _tool(files={"umowa.pdf": _PDF}, budget=len(_PDF) - 1)

    result = spec.fn(action="read", name="umowa.pdf")

    assert "budżecie" in result["error"]
    assert str(len(_PDF) - 1) in result["error"]
    assert queue.drain() == ()


def test_budget_is_shared_across_pulls_in_one_turn():
    spec, queue = _tool(files={"a.pdf": _PDF, "b.pdf": _PDF}, budget=len(_PDF))

    first = spec.fn(action="read", name="a.pdf")
    second = spec.fn(action="read", name="b.pdf")

    assert first["materialized"] is True
    assert "budżecie" in second["error"]
    assert len(queue.drain()) == 1


def test_unknown_action_is_named_not_silently_treated_as_read():
    spec, _ = _tool(files={"umowa.pdf": _PDF})

    result = spec.fn(action="skasuj", name="umowa.pdf")

    assert "skasuj" in result["error"]


def test_path_in_the_name_is_rejected_before_touching_the_disk():
    """Nazwa pochodzi od modelu; katalog rozmowy jest granicą, nie sugestią.

    Asercja celuje w KOMUNIKAT strażnika, nie w samo „error": samo „error" spełnia też
    „plik nie istnieje", więc sonda przechodziłaby po usunięciu strażnika — czyli pilnowałaby
    niczego. To ta sama pułapka, którą łatwo przeoczyć w każdym teście bezpieczeństwa.
    """
    spec, _ = _tool(files={"umowa.pdf": _PDF})

    result = spec.fn(action="read", name="../../etc/passwd")

    assert "niedozwolona nazwa" in result["error"]


def test_extractor_failure_comes_back_as_a_refusal_not_a_dead_turn():
    """Uszkodzony plik NIE MOŻE zabić tury — ADR 0064 obiecuje degradację do notki.

    Błąd ekstraktora nie dziedziczy z ``WorkMateError``, a rdzeń woła narzędzie poza ``try``
    (nieznany wyjątek = defekt kodu), więc bez osłony w materializerze jeden zepsuty dokument
    kończył turę komunikatem „chwilowy błąd" i nie zapisywał jej w pamięci.
    """

    class _Wybuchowy:
        def materialize(self, name, data):
            raise RuntimeError("czytnik dokumentu padł")

    spec, queue = _tool(files={"umowa.pdf": _PDF}, materializer=_Wybuchowy())

    with pytest.raises(RuntimeError):
        spec.fn(action="read", name="umowa.pdf")  # atrapa rzuca WPROST — patrz sonda w adapterze
    assert queue.drain() == ()


def test_readable_but_empty_file_is_a_refusal_not_a_silent_materialization():
    """„materialized: true" plus pusta etykieta: model nie wie, że nic nie dostał."""
    spec, queue = _tool(files={"pusty.txt": b"   \n  "})

    result = spec.fn(action="read", name="pusty.txt")

    assert "nie zawiera tekstu" in result["error"]
    assert queue.drain() == ()  # pusty plik nie pali budżetu


def test_text_files_are_charged_against_the_budget_too():
    """Plik zamieniony na tekst nie niesie bajtów do API, ale kontekst zajmuje.

    Bez obciążania budżetu model mógł pobierać go bez końca — także ten sam w kółko — i wysycić
    żądanie treścią, którą sam sobie podaje.
    """
    spec, queue = _tool(files={"notatka.txt": b"x" * 500}, budget=600)

    first = spec.fn(action="read", name="notatka.txt")
    second = spec.fn(action="read", name="notatka.txt")

    assert first["materialized"] is True
    assert "budżecie" in second["error"]


def test_single_file_ceiling_is_enforced_on_top_of_the_turn_budget():
    """Budżet tury nie zastępuje pułapu na JEDEN plik — inaczej jeden plik wysyca żądanie."""
    spec, _ = _tool(
        files={"umowa.pdf": _PDF},
        limits=MaterializationLimits(max_bytes=1, max_extract_bytes=10_000_000),
    )

    assert "limit rozmiaru" in spec.fn(action="read", name="umowa.pdf")["error"]


def test_oversized_file_is_refused_before_it_is_processed():
    spec, _ = _tool(
        files={"umowa.pdf": _PDF},
        limits=MaterializationLimits(max_bytes=10_000_000, max_extract_bytes=1),
    )

    assert "za duży" in spec.fn(action="read", name="umowa.pdf")["error"]


def test_scope_is_closed_over_and_invisible_to_the_model():
    """Model nie widzi scope w schemacie, więc nie ma jak sięgnąć cudzej rozmowy."""
    spec, _ = _tool(files={"umowa.pdf": _PDF})

    import typing

    assert set(typing.get_type_hints(spec.fn)) == {"action", "name", "return"}


def test_tool_is_named_file_and_carries_a_usable_description():
    spec, _ = _tool()

    assert spec.name == "File"
    assert "read" in spec.description
    # Opis mówi też, KIEDY nie używać — inaczej model sięga po nie do plików tekstowych.
    # Dokąd odesłać, zależy od drzwi (ADR 0068 §2): bez powłoki `cat` byłby obietnicą bez pokrycia.
    assert "ReadFile" in spec.description
    assert "cat" not in spec.description


# --- Akcje mutujące bazę wiedzy (ADR 0065) -------------------------------------


class _FakeMutations:
    """Atrapa bramki mutacji — notuje wywołania, oddaje sukces albo zadaną odmowę.

    ``allow_delete`` jest tu, bo od rundy 4 ADR 0068 czyta go BUDOWNICZY katalogu: bramka
    kasowania musi zamknąć ``Literal``, a nie dopiero ciało serwisu. Atrapa bez tego pola
    opisywałaby serwis, którego nie ma.
    """

    def __init__(self, refuse: str = "", allow_delete: bool = True) -> None:
        self.refuse = refuse
        self.allow_delete = allow_delete
        self.edits: list[tuple] = []
        self.deletes: list[tuple] = []
        self.origin: list[tuple] = []

    def _maybe_refuse(self, verdict_sink=None):  # noqa: ANN001, ANN202
        """Odmowa sędziego — z werdyktem zgłoszonym PRZED podniesieniem wyjątku.

        Atrapa naśladuje tu kolejność prawdziwej bramki (``NoteMutationService._decide``): werdykt
        idzie do ujścia w chwili ORZECZENIA, a wyjątek dopiero potem. Atrapa zgłaszająca po
        wyjątku opisywałaby serwis, w którym werdykt ``allow`` ginie przy nieudanym zapisie —
        czyli dokładnie ten defekt, przed którym ujście ma bronić.
        """
        if self.refuse:
            from workmate.core.application.note_mutation import MutationOutcome, MutationRefused
            from workmate.core.domain.mutation import JudgeVerdict

            if verdict_sink is not None:
                verdict_sink("confirm", self.refuse)
            raise MutationRefused(MutationOutcome(False, JudgeVerdict("confirm", self.refuse)))

    def edit_note(  # noqa: ANN001, ANN201
        self,
        note_id,
        body,
        *,
        requester,
        intent,
        turn_token="",
        trust_class="",
        tainted=True,
        verdict_sink=None,
    ):
        from workmate.core.application.note_mutation import MutationOutcome
        from workmate.core.domain.mutation import JudgeVerdict

        self._maybe_refuse(verdict_sink)
        self.edits.append((note_id, body, requester, intent))
        # Pochodzenie tury (ADR 0066) i token tury (ADR 0065) notujemy OSOBNO: dopóki atrapa je
        # połykała, narzędzie mogło przestać je przekazywać i żadna sonda by tego nie zauważyła.
        self.origin.append((turn_token, trust_class, tainted))
        if verdict_sink is not None:
            verdict_sink("allow", "ok")
        return MutationOutcome(True, JudgeVerdict("allow", "ok"), "/snap/x")

    def delete_note(  # noqa: ANN001, ANN201
        self,
        note_id,
        *,
        requester,
        intent,
        turn_token="",
        trust_class="",
        tainted=True,
        verdict_sink=None,
    ):
        from workmate.core.application.note_mutation import MutationOutcome
        from workmate.core.domain.mutation import JudgeVerdict

        self._maybe_refuse(verdict_sink)
        self.deletes.append((note_id, requester, intent))
        self.origin.append((turn_token, trust_class, tainted))
        if verdict_sink is not None:
            verdict_sink("allow", "ok")
        return MutationOutcome(True, JudgeVerdict("allow", "ok"), "/snap/x")


def _tool_z_mutacjami(
    *,
    requester: str = "Anna",
    refuse: str = "",
    turn_token: str = "",
    trust_class: str = "unknown",
    tainted=True,
    allow_delete: bool = True,
    verdict_sink=None,
):
    scope_dir = str(_SCOPE.dirpath())
    repo = _FakeWorkspace({f"{scope_dir}/umowa.pdf": _PDF})
    mutations = _FakeMutations(refuse, allow_delete=allow_delete)
    (spec,) = build_file_catalog(
        _SCOPE,
        WorkspaceService(repo),
        _FakeMaterializer(),  # type: ignore[arg-type]
        AttachmentQueue(budget_bytes=1_000_000),
        MaterializationLimits(max_bytes=10_000_000, max_extract_bytes=10_000_000),
        mutations,  # type: ignore[arg-type]
        requester,
        trust_class,
        tainted,
        turn_token,
        False,
        verdict_sink,
    )
    return spec, mutations


# --- Werdykt sędziego trafia do wiersza audytu (ADR 0065 §8, znalezisko 9.11) ---------


def test_an_allowed_edit_reports_its_verdict_to_the_audit_sink():
    """Zgoda też jest orzeczeniem. Dziennik z samymi odmowami każe operatorowi wnioskować
    o zgodach z ich NIEOBECNOŚCI — nieodróżnialnej od sędziego, który nie biegł."""
    zgloszone: list[tuple[str, str]] = []
    spec, _ = _tool_z_mutacjami(verdict_sink=lambda v, r: zgloszone.append((v, r)))

    spec.fn(action="edit", name="biap/mpwik/2026-08-01-x", content="nowa", reason="literówka")

    assert zgloszone == [("allow", "ok")]


def test_a_refused_edit_reports_the_verdict_and_the_reason():
    """Odmowa niesie POWÓD — po to ta kolumna istnieje; sam werdykt nie tłumaczy niczego.

    Atrapa odmawia werdyktem ``confirm`` (zapowiedź czekająca na powtórzenie z innej tury) —
    i to jest właśnie ten wiersz, którego brak najbardziej boli: bez niego zapowiedź, która
    nigdy nie wróciła, nie zostawia w dzienniku żadnego śladu.
    """
    zgloszone: list[tuple[str, str]] = []
    spec, _ = _tool_z_mutacjami(
        refuse="notatka opisuje inny projekt",
        verdict_sink=lambda v, r: zgloszone.append((v, r)),
    )

    spec.fn(action="edit", name="biap/mpwik/2026-08-01-x", content="nowa", reason="bo tak")

    assert zgloszone == [("confirm", "notatka opisuje inny projekt")]


def test_an_allowed_delete_reports_its_verdict_too():
    zgloszone: list[tuple[str, str]] = []
    spec, _ = _tool_z_mutacjami(verdict_sink=lambda v, r: zgloszone.append((v, r)))

    spec.fn(action="delete", name="biap/mpwik/2026-08-01-x", reason="duplikat")

    assert zgloszone == [("allow", "ok")]


def test_refusals_BEFORE_the_judge_report_nothing():
    """Brak powodu odbija się PRZED sędzią, więc werdyktu nie ma i nie wolno go udawać —
    wiersz „deny" bez orzeczenia kłamałby o tym, że sędzia w ogóle się wypowiedział."""
    zgloszone: list[tuple[str, str]] = []
    spec, _ = _tool_z_mutacjami(verdict_sink=lambda v, r: zgloszone.append((v, r)))

    spec.fn(action="edit", name="biap/mpwik/2026-08-01-x", content="nowa", reason="   ")

    assert zgloszone == []


def test_a_broken_audit_sink_never_costs_the_mutation():
    """Audyt jest poboczny (ADR 0067 §1.1): jego awaria nie może zamienić udanej zmiany w błąd."""

    def sink_ktory_pada(_v: str, _r: str) -> None:
        raise RuntimeError("baza audytu zablokowana")

    spec, mutations = _tool_z_mutacjami(verdict_sink=sink_ktory_pada)

    result = spec.fn(
        action="edit", name="biap/mpwik/2026-08-01-x", content="nowa", reason="literówka"
    )

    assert result == {"edited": True, "id": "biap/mpwik/2026-08-01-x"}
    assert len(mutations.edits) == 1


def test_mutation_actions_are_absent_from_the_schema_when_the_gate_is_closed():
    """Model widzi zdolności przez SCHEMAT, nie przez opis — więc bada się schemat.

    Wcześniejsza wersja tej sondy sprawdzała sam opis i twierdziła „model nie zobaczy nawet
    nazwy akcji mutującej", podczas gdy enum sygnatury wystawiał `edit`/`delete` na każdych
    drzwiach. Sonda przechodziła, twierdzenie było nieprawdziwe.
    """
    import typing

    zamknieta, _ = _tool()
    otwarta, _ = _tool_z_mutacjami()

    # ``get_type_hints``, nie ``__annotations__``: moduł ma ``from __future__ import annotations``,
    # więc surowe adnotacje są NAPISAMI — a schemat dla modelu powstaje z rozwiązanych typów.
    zamk = typing.get_type_hints(zamknieta.fn)["action"]
    otw = typing.get_type_hints(otwarta.fn)["action"]
    assert typing.get_args(zamk) == ("read",)
    assert set(typing.get_args(otw)) == {"read", "edit", "delete"}
    assert "edit" not in zamknieta.description


def test_unrecognised_requester_gets_no_mutation():
    """Fail-closed jak przy bramce powłoki (ADR 0063): bez rozpoznanego człowieka nie ma komu
    przypisać zmiany ani kogo zapytać o potwierdzenie."""
    spec, mutations = _tool_z_mutacjami(requester="")

    wynik = spec.fn(action="edit", name="notatka", content="nowa", reason="poprawka")

    assert "Nie rozpoznaję" in wynik["error"]
    assert mutations.edits == []


def test_edit_requires_a_reason():
    """Bez powodu nie ma czego oceniać — sędzia dostałby pustą deklarację intencji."""
    spec, mutations = _tool_z_mutacjami()

    wynik = spec.fn(action="edit", name="notatka", content="nowa", reason="  ")

    assert "reason" in wynik["error"]
    assert mutations.edits == []


def test_empty_content_is_refused_instead_of_silently_emptying_the_note():
    """Pusta treść skasowałaby notatkę pod pozorem edycji — kasowanie ma być świadome."""
    spec, mutations = _tool_z_mutacjami()

    wynik = spec.fn(action="edit", name="notatka", content="   ", reason="porządki")

    assert "delete" in wynik["error"]
    assert mutations.edits == []


def test_edit_passes_the_note_id_and_reason_through():
    spec, mutations = _tool_z_mutacjami()

    wynik = spec.fn(action="edit", name="biap/mpwik/x", content="nowa", reason="poprawka")

    assert wynik["edited"] is True
    assert mutations.edits == [("biap/mpwik/x", "nowa", "Anna", "poprawka")]


def test_delete_reports_where_the_copy_is():
    """Model ma powiedzieć człowiekowi, gdzie leży kopia — cofnięcie nie ma być śledztwem."""
    spec, _ = _tool_z_mutacjami()

    wynik = spec.fn(action="delete", name="biap/mpwik/x", reason="duplikat")

    assert wynik["deleted"] is True
    assert wynik["kopia"] == "snap/x"  # bez układu katalogów hosta


def test_refusal_comes_back_as_a_result_not_as_a_tool_failure():
    """Odmowa sędziego to NORMALNY wynik z powodem, który model ma przekazać człowiekowi —
    nie awaria narzędzia, po której model zacznie ponawiać."""
    spec, _ = _tool_z_mutacjami(refuse="to skasowałoby ustalenia z całego kwartału")

    wynik = spec.fn(action="delete", name="biap/mpwik/x", reason="porządki")

    assert wynik["verdict"] == "confirm"
    assert wynik["wymaga_potwierdzenia"] is True
    assert "kwartału" in wynik["error"]


# --- Szew 0064/0065 ↔ 0066: pochodzenie tury i token tury jadą do bramki mutacji ---


def test_the_turn_token_reaches_the_mutation_gate():
    """Punkt kontrolny człowieka stoi CAŁY na tym tokenie: bez niego bramka nie odróżni
    powtórzenia z nowej tury od ponowienia w tej samej pętli narzędzi (do ośmiu rund)."""
    spec, mutations = _tool_z_mutacjami(turn_token="tura-42")

    spec.fn(action="edit", name="biap/mpwik/x", content="nowa", reason="poprawka")

    assert mutations.origin == [("tura-42", "unknown", True)]


def test_the_trust_class_and_taint_of_the_turn_reach_the_mutation_gate():
    """Sędzia ma widzieć, że zmianę zleca tura po przeczytaniu obcego pliku — to eskaluje ocenę."""
    spec, mutations = _tool_z_mutacjami(turn_token="t1", trust_class="T2", tainted=True)

    spec.fn(action="delete", name="biap/mpwik/x", reason="porządki")

    assert mutations.origin == [("t1", "T2", True)]


def test_a_clean_turn_is_reported_as_clean_not_flattened_to_the_safe_default():
    """Odwrotny kierunek tej samej sondy: gdyby narzędzie zawsze wysyłało domyślne
    „skażona/unknown", klasa pochodzenia byłaby ozdobnikiem, a nie sygnałem."""
    spec, mutations = _tool_z_mutacjami(turn_token="t1", trust_class="T1", tainted=False)

    spec.fn(action="edit", name="biap/mpwik/x", content="nowa", reason="x")

    assert mutations.origin == [("t1", "T1", False)]


def test_the_taint_is_read_AT_CALL_TIME_not_frozen_when_the_catalog_is_built():
    """Katalog powstaje raz, na starcie tury; skaza zapala się z faktów TEJ tury — później.

    Ta kolejność nie jest teoretyczna: drzwi budują narzędzia, a dopiero potem oznaczają rozmowę
    jako skażoną. Wartość domknięta przy budowie opisuje więc stan SPRZED tury. Członek dostaje
    zatruty PDF, w tej samej turze robi `File(read)` i `File(edit)` — a sędzia widzi „rozmowa
    czysta", dokładnie w turze, dla której ADR 0066 R2 tę eskalację wprowadził.

    Stąd kontrakt: ``tainted`` wolno podać jako funkcję, a fabryka ma ją wołać przy mutacji.
    """
    skaza = {"tainted": False}
    spec, mutations = _tool_z_mutacjami(
        turn_token="t1", trust_class="T1", tainted=lambda: skaza["tainted"]
    )

    skaza["tainted"] = True  # załącznik wjechał do rozmowy PO zbudowaniu narzędzi
    spec.fn(action="edit", name="biap/mpwik/x", content="nowa", reason="x")

    assert mutations.origin == [("t1", "T1", True)]


def test_a_plain_bool_still_works_so_existing_doors_are_not_broken():
    """Wartość ma dalej działać — inaczej zmiana kontraktu byłaby cichym zerwaniem okablowania."""
    spec, mutations = _tool_z_mutacjami(turn_token="t1", trust_class="T1", tainted=True)

    spec.fn(action="edit", name="biap/mpwik/x", content="nowa", reason="x")

    assert mutations.origin == [("t1", "T1", True)]


def test_defaults_of_the_factory_are_the_safe_ones():
    """Drzwi, które o 0066 jeszcze nie wiedzą, mają dostać ściśle bezpieczny domysł."""
    scope_dir = str(_SCOPE.dirpath())
    mutations = _FakeMutations()
    (spec,) = build_file_catalog(
        _SCOPE,
        WorkspaceService(_FakeWorkspace({f"{scope_dir}/umowa.pdf": _PDF})),
        _FakeMaterializer(),  # type: ignore[arg-type]
        AttachmentQueue(budget_bytes=1_000_000),
        MaterializationLimits(max_bytes=10_000_000, max_extract_bytes=10_000_000),
        mutations,  # type: ignore[arg-type]
        "Anna",
    )

    spec.fn(action="edit", name="biap/mpwik/x", content="nowa", reason="x")

    assert mutations.origin == [("", "unknown", True)]


# --- Obrona w głąb: zamknięta bramka odmawia także wtedy, gdy akcja ominie schemat ---


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_a_smuggled_mutation_is_refused_by_the_read_only_door(action: str):
    """Schemat NIE jest granicą — jest podpowiedzią. Runtime dostaje argumenty od modelu i
    ``Literal`` niczego w czasie działania nie wymusza, więc zamknięta bramka musi odmówić
    także wtedy, gdy akcja przyjedzie mimo braku w enumie.
    """
    spec, queue = _tool(files={"umowa.pdf": _PDF})

    wynik = spec.fn(action=action, name="biap/mpwik/x")

    assert "wyłączone" in wynik["error"]
    assert queue.drain() == ()


def test_the_read_only_door_names_the_alternative_instead_of_just_refusing():
    """Odmowa ma prowadzić do wyjścia — inaczej model ponawia albo zmyśla, że zapisał."""
    spec, _ = _tool(files={"umowa.pdf": _PDF})

    wynik = spec.fn(action="edit", name="biap/mpwik/x")

    assert "nową notatkę" in wynik["error"]


# --- Opis odsyla tam, gdzie zdolnosc faktycznie jest (ADR 0068 §2) --------------------


def _opis(*, shell: bool, mutacje: bool = False) -> str:
    repo = _FakeWorkspace({})
    (spec,) = build_file_catalog(
        _SCOPE,
        WorkspaceService(repo),
        _FakeMaterializer(),  # type: ignore[arg-type]
        AttachmentQueue(budget_bytes=1),
        MaterializationLimits(max_bytes=1, max_extract_bytes=1),
        _FakeMutations() if mutacje else None,  # type: ignore[arg-type]
        "u-anna" if mutacje else "",
        shell_available=shell,
    )
    return spec.description


def test_bez_powloki_opis_odsyla_do_narzedzi_katalogu_roboczego():
    """`cat` bez `Bash` to obietnica bez pokrycia — i to w stanie DOMYSLNYM produkcji."""
    opis = _opis(shell=False)

    assert "ReadFile" in opis and "ListFiles" in opis
    assert "cat" not in opis


def test_z_powloka_opis_odsyla_do_powloki():
    opis = _opis(shell=True)

    assert "cat" in opis
    assert "ReadFile" not in opis


def test_zrodlo_identyfikatora_notatki_zalezy_od_powloki():
    """Z powloka narzedzia odczytu SA ZDJETE, wiec odeslanie do nich bylo puste w druga strone."""
    z_powloka = _opis(shell=True, mutacje=True)
    bez_powloki = _opis(shell=False, mutacje=True)

    assert "workmate-search" in z_powloka and "SearchNotes" not in z_powloka
    assert "SearchNotes" in bez_powloki and "workmate-search" not in bez_powloki


def test_brak_pliku_niesie_podpowiedz_jak_brak_pola():
    """Ksztalt `tool`/`action`/`hint` ten sam co przy bledzie wywolania (ADR 0068 §9)."""
    spec, _ = _tool(files={})

    wynik = spec.fn(action="read", name="nie-ma.pdf")

    assert wynik["status"] == "not_found"
    assert wynik["tool"] == "File"
    assert wynik["action"] == "read"
    assert "ListFiles" in wynik["hint"]


# --- Bramka kasowania siedzi w `Literal`, nie w ciele (ADR 0068, runda 4) --------------


def test_delete_nie_istnieje_w_schemacie_gdy_kasowanie_jest_wylaczone():
    """Trzeci wariant sygnatury, bo bramki sa DWIE i niezalezne.

    `..._ENABLE_NOTE_MUTATION=true` + `..._ENABLE_NOTE_DELETE=false` to konfiguracja domyslna po
    wlaczeniu mutacji (ADR 0065 wiaze kasowanie z dzialajaca kopia zapasowa). Do tej rundy model
    widzial w enumie `delete`, probowal go uzyc i tracil runde narzedziowa na odmowe z ciala —
    czyli dokladnie to, czemu miala zapobiec regula „bramka w `Literal`, nie w ciele".
    """
    import typing

    spec, _ = _tool_z_mutacjami(allow_delete=False)

    akcje = typing.get_args(typing.get_type_hints(spec.fn)["action"])

    assert set(akcje) == {"read", "edit"}
    assert "delete" not in spec.description


def test_delete_jest_w_schemacie_gdy_kasowanie_jest_wlaczone():
    import typing

    spec, _ = _tool_z_mutacjami(allow_delete=True)

    akcje = typing.get_args(typing.get_type_hints(spec.fn)["action"])

    assert set(akcje) == {"read", "edit", "delete"}


def test_opis_reklamuje_delete_dokladnie_wtedy_gdy_akcja_istnieje():
    """Opis i `Literal` musza mowic to samo — inaczej model dostaje sprzecznosc w jednym miejscu."""
    z_kasowaniem = _tool_z_mutacjami(allow_delete=True)[0].description
    bez_kasowania = _tool_z_mutacjami(allow_delete=False)[0].description

    assert "`delete`" in z_kasowaniem
    assert "`delete`" not in bez_kasowania
    assert "`edit`" in bez_kasowania  # edycja zostaje — to osobna bramka


def test_przemycone_delete_przy_zamknietej_bramce_dostaje_odmowe_ze_wskazaniem_wyjscia():
    """Obrona w glab: `spec.fn` wola tez kod aplikacji, z pominieciem koercji argumentow.

    Odmowa jest MERYTORYCZNA (co zrobic zamiast), nie „nie ma takiej akcji" — bo akcja istnieje
    w produkcie, tylko nie na tych drzwiach. Serwis odrzucilby to samo, ale komunikatem pisanym
    do operatora.
    """
    spec, mutations = _tool_z_mutacjami(allow_delete=False)

    wynik = spec.fn(action="delete", name="biap/mpwik/x", reason="po co")

    assert "Usuwanie notatek jest wyłączone" in wynik["error"]
    assert "`edit`" in wynik["error"]
    assert mutations.deletes == []  # serwis NIE zostal zawolany


def test_edycja_dziala_normalnie_przy_zamknietej_bramce_kasowania():
    """Bramki sa niezalezne — zamkniecie kasowania nie moze zabrac edycji."""
    spec, mutations = _tool_z_mutacjami(allow_delete=False)

    wynik = spec.fn(action="edit", name="biap/mpwik/x", content="nowa tresc", reason="poprawka")

    assert wynik["edited"] is True
    assert len(mutations.edits) == 1


def test_odmowa_nieznanej_akcji_nie_podpowiada_delete_gdy_go_nie_ma():
    """Lista dozwolonych jedzie z tego samego zrodla co `Literal` — inaczej odmowa reklamuje
    zdolnosc, ktorej schemat nie ma (ten sam blad co w wariancie odczytu `Project`)."""
    spec, _ = _tool_z_mutacjami(allow_delete=False)

    wynik = spec.fn(action="wymyslona", name="x")

    assert wynik["allowed"] == ["read", "edit"]
    assert "delete" not in wynik["hint"]
