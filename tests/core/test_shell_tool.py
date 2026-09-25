"""Narzędzie ``Bash`` (ADR 0057): wiązanie katalogu rozmowy, przenoszenie flag i opis montaży.

Powłoka biegnie w osobnym kontenerze, więc tutaj sprawdzamy wyłącznie warstwę aplikacyjną:
co narzędzie przekazuje wykonawcy i co oddaje modelowi. Sam wykonawca ma własne testy
(``tests/adapters/test_exec_runner.py``), które biegną w obrazie — są POSIX-only.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field

from sufler.core.application.tools import build_shell_catalog
from sufler.core.domain.workspace import WorkspaceScope
from sufler.core.errors import SuflerError
from sufler.core.ports.command import CommandResult


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
    """Katalog rozmowy jest DOMKNIĘTY w closurze — schemat niesie sam ``command``/``timeout_s``.

    Sondujemy przez ``inspect.signature``, nie przez ``__code__.co_varnames[:co_argcount]``:
    ``co_argcount`` NIE liczy parametrów keyword-only, więc dołożenie ``*, scope_dir``
    przeszłoby tamtą asercję niezauważone — czyli model dostałby drogę do cudzej rozmowy,
    a sonda dalej świeciłaby na zielono.
    """
    spec = _tool(FakeRunner())

    assert set(inspect.signature(spec.fn).parameters) == {"command", "timeout_s"}


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


def test_a_NEGATIVE_timeout_is_a_readable_refusal_not_an_instant_timeout():
    """``timeout_s`` przychodzi OD MODELU i nie miał dolnej granicy.

    Wartość ujemna jechała wprost do wykonawcy, który oddawał ``timed_out`` bez uruchomienia
    polecenia. Model czytał to jako „polecenie za wolne" i poprawiał NIE TEN parametr —
    zamiast dowiedzieć się, że podał złą liczbę. Polecenie nie ma się wtedy w ogóle odpalić.
    """
    runner = FakeRunner()

    wynik = _tool(runner, default_timeout_s=45).fn(command="ls", timeout_s=-5)

    assert "timeout_s" in wynik["error"] and "45" in wynik["error"]
    assert runner.calls == [], "polecenie nie może pójść do wykonawcy z ujemnym budżetem"


def test_zero_still_means_use_the_default_not_a_refusal():
    """Zero jest umowne (schemat ma domyślne 0) — odmowa na nim zablokowałaby zwykłe wywołanie."""
    runner = FakeRunner()

    _tool(runner, default_timeout_s=45).fn(command="ls", timeout_s=0)

    assert runner.calls[0][2] == 45.0


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
            raise SuflerError("gniazdo zniknęło")

    result = build_shell_catalog(
        WorkspaceScope("cli", "x"), ExplodingRunner(), workspace_root="/home/scratchpad"
    )[0].fn(command="ls")

    assert result == {"error": "gniazdo zniknęło"}


def test_description_carries_running_facts_and_leaves_the_map_to_the_prompt():
    """Po etapie 6 opis niesie URUCHAMIANIE, a układ montaży — sekcja ``ENVIRONMENT`` promptu.

    Poprzednia wersja tej sondy zamrażała tu mapę i sama zapowiadała przeprowadzkę („prompt
    dostanie go w §6"). Trzymanie mapy w obu miejscach dałoby dwa źródła do synchronizacji,
    więc asercja na komplet ścieżek stoi teraz w ``tests/core/test_prompt.py`` — razem
    z bramką wiążącą ją z tym narzędziem.
    """
    description = _tool(FakeRunner()).description

    assert "/home/scratchpad" in description, "katalog startowy to fakt o URUCHOMIENIU polecenia"
    assert "sufler-search" in description
    assert "64 KB" in description

    # Mapa wyprowadzona: gdyby wróciła tutaj, prompt i opis rozjechałyby się przy następnym montażu.
    for path in ("/mnt/system/notes/", "/mnt/system/projects/", "/mnt/skills/"):
        assert path not in description, f"{path} należy do ENVIRONMENT, nie do opisu narzędzia"


def test_description_promises_the_outbox_ONLY_when_delivery_exists():
    """Dostawa ma WŁASNĄ bramkę na drzwiach, niezależną od powłoki, i obie są domyślnie off.

    Konfiguracja „powłoka tak, załączniki nie" jest realna, a opis obiecujący w niej dostawę
    byłby dokładnie tym defektem, który ta zdolność likwiduje: model dostaje kod 0 i ciszę,
    a pliki rosną na wolumenie, którego nikt nie opróżnia.
    """
    with_delivery = build_shell_catalog(
        WorkspaceScope("cli", "x"),
        FakeRunner(),
        workspace_root="/home/scratchpad",
        outbox_enabled=True,
    )[0].description
    without = _tool(FakeRunner()).description

    assert "outputs/" in with_delivery
    assert "md/txt/pdf/docx" in with_delivery, "biała lista składana z jednoźródłowej mapy formatów"
    assert "outputs/" not in without


def test_description_does_not_promise_unimplemented_mounts():
    """Regresja: opis obiecywał `/mnt/user/{inputs,outputs}`, których ŻADEN kod nie obsługiwał.

    Wolumen był zamontowany w compose, więc `ls` działał, a zapis kończył się zerem i ciszą —
    model dostawał potwierdzenie dostawy, która nigdy nie następowała. Ta asercja pilnuje,
    żeby martwa ścieżka nie wróciła do opisu razem z jakimś przyszłym montażem.
    """
    for description in (
        _tool(FakeRunner()).description,
        build_shell_catalog(
            WorkspaceScope("cli", "x"),
            FakeRunner(),
            workspace_root="/home/scratchpad",
            outbox_enabled=True,
        )[0].description,
    ):
        assert "/mnt/user" not in description


def test_workspace_root_without_trailing_slash_does_not_double_it():
    """Korzeń z konfiguracji bywa zapisany ze slashem na końcu — ścieżka ma zostać jedna."""
    runner = FakeRunner()

    _tool(runner, workspace_root="/home/scratchpad/").fn(command="pwd")

    assert "//" not in runner.calls[0][1]


# --- Cisza po udanym poleceniu (ADR 0068 §8) ------------------------------------------


def test_udane_polecenie_bez_wyjscia_nazywa_pustke_wprost():
    """Same puste napisy czytaja sie jak awaria i zapraszaja do powtorki tego samego polecenia.

    `mkdir`, `mv` i przekierowanie do pliku konczy sie kodem 0 i cisza — to NORMALNY wynik.
    Wzorzec jak `count` w `search_notes`: pusty zbior ma byc widoczny jako zbior pusty.
    """
    runner = FakeRunner(result=CommandResult(exit_code=0, stdout="", stderr=""))

    wynik = _tool(runner).fn(command="mkdir raporty")

    assert wynik["exit_code"] == 0
    assert "nic nie wypisa" in wynik["note"]


def test_polecenie_z_wyjsciem_nie_dostaje_notki():
    """Notka jest o CISZY — dopisana do kazdego wyniku bylaby szumem w kazdej turze."""
    wynik = _tool(FakeRunner(result=CommandResult(exit_code=0, stdout="plik.md", stderr=""))).fn(
        command="ls"
    )

    assert "note" not in wynik


def test_niepowodzenie_bez_wyjscia_nie_dostaje_notki_o_sukcesie():
    """Kod niezerowy i cisza to co innego niz sukces i cisza — notka nie ma tego zacierac."""
    wynik = _tool(FakeRunner(result=CommandResult(exit_code=1, stdout="", stderr=""))).fn(
        command="false"
    )

    assert "note" not in wynik
