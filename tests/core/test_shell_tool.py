"""Narzędzie ``Bash`` (ADR 0057): wiązanie katalogu rozmowy, przenoszenie flag i opis montaży.

Powłoka biegnie w osobnym kontenerze, więc tutaj sprawdzamy wyłącznie warstwę aplikacyjną:
co narzędzie przekazuje wykonawcy i co oddaje modelowi. Sam wykonawca ma własne testy
(``tests/adapters/test_exec_runner.py``), które biegną w obrazie — są POSIX-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from workmate.core.application.tools import build_shell_catalog
from workmate.core.domain.workspace import WorkspaceScope
from workmate.core.errors import WorkMateError
from workmate.core.ports.command import CommandResult


@dataclass
class FakeRunner:
    """Atrapa ``CommandRunner`` zapisująca, co dostała, i oddająca zadany wynik."""

    result: CommandResult = field(
        default_factory=lambda: CommandResult(exit_code=0, stdout="ok", stderr="")
    )
    calls: list[tuple[str, str, float]] = field(default_factory=list)

    def run(self, command: str, *, cwd: str = "", timeout_s: float = 0) -> CommandResult:
        self.calls.append((command, cwd, timeout_s))
        return self.result


def _tool(runner: FakeRunner, **kwargs):
    catalog = build_shell_catalog(
        WorkspaceScope("teams_graph", "team/kanal/watek"),
        runner,
        workspace_root=kwargs.pop("workspace_root", "/home/scratchpad"),
        **kwargs,
    )
    return catalog[0]


def test_catalog_exposes_a_single_tool_named_bash():
    """Jedno narzędzie, nazwa docelowa z planu konsolidacji — bez przemianowania w etapie 5."""
    catalog = build_shell_catalog(
        WorkspaceScope("cli", "x"), FakeRunner(), workspace_root="/home/scratchpad"
    )

    assert [spec.name for spec in catalog] == ["Bash"]


def test_working_directory_is_bound_to_the_conversation_scope():
    """``cwd`` wyliczamy z ZAUFANEGO scope; model nie podaje go i nie sięgnie cudzej rozmowy."""
    runner = FakeRunner()
    scope = WorkspaceScope("teams_graph", "team/kanal/watek")

    _tool(runner).fn(command="ls")

    _, cwd, _ = runner.calls[0]
    assert cwd == f"/home/scratchpad/{scope.dirpath()}"


def test_scope_is_absent_from_the_tool_schema():
    """Katalog rozmowy jest DOMKNIĘTY w closurze — schemat niesie sam ``command``/``timeout_s``."""
    spec = _tool(FakeRunner())

    names = spec.fn.__code__.co_varnames[: spec.fn.__code__.co_argcount]

    assert set(names) == {"command", "timeout_s"}


def test_different_conversations_get_different_directories():
    """Izolacja rozmów: dwa wątki tego samego kanału startują w różnych katalogach."""
    first, second = FakeRunner(), FakeRunner()

    build_shell_catalog(
        WorkspaceScope("teams_graph", "a"), first, workspace_root="/home/scratchpad"
    )[0].fn(command="pwd")
    build_shell_catalog(
        WorkspaceScope("teams_graph", "b"), second, workspace_root="/home/scratchpad"
    )[0].fn(command="pwd")

    assert first.calls[0][1] != second.calls[0][1]


def test_default_timeout_applies_when_the_model_omits_it():
    """Bez ``timeout_s`` od modelu idzie wartość z konfiguracji, nie zero."""
    runner = FakeRunner()

    _tool(runner, default_timeout_s=45).fn(command="sleep 1")

    assert runner.calls[0][2] == 45.0


def test_model_timeout_overrides_the_default():
    """Model bywa jedynym, kto wie, czy odpala ``ls``, czy przetwarzanie — może podnieść limit."""
    runner = FakeRunner()

    _tool(runner, default_timeout_s=45).fn(command="long", timeout_s=120)

    assert runner.calls[0][2] == 120.0


def test_degradation_flags_reach_the_model_separately_from_exit_code():
    """Flagi degradacji są OSOBNE od kodu wyjścia — inaczej fragment czyta się jak całość."""
    runner = FakeRunner(
        result=CommandResult(
            exit_code=-9, stdout="poczatek", stderr="", truncated=True, timed_out=True
        )
    )

    result = _tool(runner).fn(command="cat wielki-plik")

    assert result == {
        "exit_code": -9,
        "stdout": "poczatek",
        "stderr": "",
        "truncated": True,
        "timed_out": True,
    }


def test_nonzero_exit_is_a_result_not_an_error():
    """Nieudane polecenie wraca jako WYNIK — dla modelu to porażka do poprawienia w nowej turze."""
    runner = FakeRunner(result=CommandResult(exit_code=127, stdout="", stderr="nie ma takiej"))

    result = _tool(runner).fn(command="nieistniejace")

    assert result["exit_code"] == 127
    assert "error" not in result


def test_runner_failure_is_wrapped_into_an_error_envelope():
    """Wyjątek domenowy z warstwy niżej nie może wywrócić tury — wraca kopertą ``error``."""

    class ExplodingRunner:
        def run(self, command: str, *, cwd: str = "", timeout_s: float = 0) -> CommandResult:
            raise WorkMateError("gniazdo zniknęło")

    result = build_shell_catalog(
        WorkspaceScope("cli", "x"), ExplodingRunner(), workspace_root="/home/scratchpad"
    )[0].fn(command="ls")

    assert result == {"error": "gniazdo zniknęło"}


def test_description_carries_the_mount_map_and_the_search_command():
    """Opis jest jedynym miejscem, z którego model pozna układ montaży — prompt dostanie go w §6."""
    description = _tool(FakeRunner()).description

    for path in (
        "/home/scratchpad",
        "/mnt/system/notes/",
        "/mnt/system/projects/",
        "/mnt/user/inputs/",
        "/mnt/user/outputs/",
    ):
        assert path in description
    assert "workmate-search" in description
    assert "64 KB" in description


def test_workspace_root_without_trailing_slash_does_not_double_it():
    """Korzeń z konfiguracji bywa zapisany ze slashem na końcu — ścieżka ma zostać jedna."""
    runner = FakeRunner()

    _tool(runner, workspace_root="/home/scratchpad/").fn(command="pwd")

    assert "//" not in runner.calls[0][1]
