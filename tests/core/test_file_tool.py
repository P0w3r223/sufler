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

    assert set(spec.fn.__annotations__) == {"action", "name", "content", "reason", "return"}


def test_tool_is_named_file_and_carries_a_usable_description():
    spec, _ = _tool()

    assert spec.name == "File"
    assert "read" in spec.description
    # Opis mówi też, KIEDY nie używać — inaczej model sięga po nie do plików tekstowych.
    assert "cat" in spec.description


# --- Akcje mutujące bazę wiedzy (ADR 0065) -------------------------------------


class _FakeMutations:
    """Atrapa bramki mutacji — notuje wywołania, oddaje sukces albo zadaną odmowę."""

    def __init__(self, refuse: str = "") -> None:
        self.refuse = refuse
        self.edits: list[tuple] = []
        self.deletes: list[tuple] = []

    def _maybe_refuse(self):
        if self.refuse:
            from workmate.core.application.note_mutation import MutationOutcome, MutationRefused
            from workmate.core.domain.mutation import JudgeVerdict

            raise MutationRefused(MutationOutcome(False, JudgeVerdict("confirm", self.refuse)))

    def edit_note(self, note_id, body, *, requester, intent):  # noqa: ANN001, ANN201
        self._maybe_refuse()
        self.edits.append((note_id, body, requester, intent))

    def delete_note(self, note_id, *, requester, intent):  # noqa: ANN001, ANN201
        from workmate.core.application.note_mutation import MutationOutcome
        from workmate.core.domain.mutation import JudgeVerdict

        self._maybe_refuse()
        self.deletes.append((note_id, requester, intent))
        return MutationOutcome(True, JudgeVerdict("allow", "ok"), "/snap/x")


def _tool_z_mutacjami(*, requester: str = "Anna", refuse: str = ""):
    scope_dir = str(_SCOPE.dirpath())
    repo = _FakeWorkspace({f"{scope_dir}/umowa.pdf": _PDF})
    mutations = _FakeMutations(refuse)
    (spec,) = build_file_catalog(
        _SCOPE,
        WorkspaceService(repo),
        _FakeMaterializer(),  # type: ignore[arg-type]
        AttachmentQueue(budget_bytes=1_000_000),
        MaterializationLimits(max_bytes=10_000_000, max_extract_bytes=10_000_000),
        mutations,  # type: ignore[arg-type]
        requester,
    )
    return spec, mutations


def test_mutation_actions_are_absent_when_the_gate_is_closed():
    """Bez bramki narzędzie ma TYLKO odczyt — model nie zobaczy nawet nazwy akcji mutującej."""
    spec, _ = _tool()

    assert "edit" not in spec.description and "delete" not in spec.description
    assert "error" in spec.fn(action="edit", name="x", content="y", reason="z")


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

    assert wynik["deleted"] is True and wynik["kopia"] == "/snap/x"


def test_refusal_comes_back_as_a_result_not_as_a_tool_failure():
    """Odmowa sędziego to NORMALNY wynik z powodem, który model ma przekazać człowiekowi —
    nie awaria narzędzia, po której model zacznie ponawiać."""
    spec, _ = _tool_z_mutacjami(refuse="to skasowałoby ustalenia z całego kwartału")

    wynik = spec.fn(action="delete", name="biap/mpwik/x", reason="porządki")

    assert wynik["verdict"] == "confirm"
    assert wynik["wymaga_potwierdzenia"] is True
    assert "kwartału" in wynik["error"]
