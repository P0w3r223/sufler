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

from workmate.core.application.tools import build_file_catalog
from workmate.core.application.workspace import WorkspaceService
from workmate.core.domain.workspace import WorkspaceFile, WorkspaceScope
from workmate.core.ports.llm import Attachment, AttachmentQueue

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
    """Port materializacji: PDF → dokument, ``.nieznany`` → None (format nieobsługiwany)."""

    def materialize(self, name: str, data: bytes) -> tuple[Attachment, int] | None:
        if not name.endswith(".pdf"):
            return None
        return Attachment("document", "application/pdf", name, data_base64="AAAA"), len(data)


def _tool(*, files: dict[str, bytes] | None = None, budget: int = 1_000_000):
    scope_dir = str(_SCOPE.dirpath())
    repo = _FakeWorkspace({f"{scope_dir}/{n}": d for n, d in (files or {}).items()})
    queue = AttachmentQueue(budget_bytes=budget)
    (spec,) = build_file_catalog(_SCOPE, WorkspaceService(repo), _FakeMaterializer(), queue)
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
    """Nazwa pochodzi od modelu; katalog rozmowy jest granicą, nie sugestią."""
    spec, _ = _tool(files={"umowa.pdf": _PDF})

    result = spec.fn(action="read", name="../../etc/passwd")

    assert "error" in result


def test_scope_is_closed_over_and_invisible_to_the_model():
    """Model nie widzi scope w schemacie, więc nie ma jak sięgnąć cudzej rozmowy."""
    spec, _ = _tool(files={"umowa.pdf": _PDF})

    assert set(spec.fn.__annotations__) == {"action", "name", "return"}


def test_tool_is_named_file_and_carries_a_usable_description():
    spec, _ = _tool()

    assert spec.name == "File"
    assert "read" in spec.description
    # Opis mówi też, KIEDY nie używać — inaczej model sięga po nie do plików tekstowych.
    assert "cat" in spec.description
