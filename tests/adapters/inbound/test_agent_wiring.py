"""Testy uwspólnionego wiringu drzwi (``agent_wiring``).

Bez klucza Claude API i bez extra ``agent``: runtime jest podmieniany atrapą przez
monkeypatch, więc żaden import SDK/klienta LLM się nie odpala. Sprawdzamy: katalog
read-only bez ``save_note``, złożenie respondera (``SafeResponder`` vs goły) oraz że
komenda ``/pomoc`` idzie przez router BEZ wołania runtime. Osobno: brak extra ``agent``
→ czytelny ``SystemExit``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from workmate.adapters.inbound import agent_wiring
from workmate.adapters.inbound.agent_wiring import (
    build_agent_runtime_or_exit,
    build_conversational_responder,
    build_read_catalog,
)
from workmate.adapters.inbound.responder import (
    ConversationalResponder,
    InboundMessage,
    SafeResponder,
)
from workmate.config import AgentSettings, ConversationSettings, Settings

_REGISTRY = """projects:
  - key: workmate
    company: biap
    name: WorkMate
    description: Asystent wiedzy
    status: active
    health: green
    phase: Faza 2
    summary: Prace w toku
    last_updated: 2025-06-24
"""


def _settings(tmp_path: Path) -> Settings:
    """Realne ``Settings`` z tmp katalogami notatek/rejestru (fns katalogu są leniwe)."""
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    registry = tmp_path / "registry.yaml"
    registry.write_text(_REGISTRY, encoding="utf-8")
    return Settings(
        data_dir=tmp_path,
        notes_dir=notes_dir,
        projects_registry=registry,
        transport="stdio",
        log_level="INFO",
        enable_write=True,
        tokens_file=tmp_path / "tokens.json",
        bind_host="127.0.0.1",
        bind_port=8000,
        allowed_hosts=("127.0.0.1:*",),
        allowed_origins=(),
        tls_certfile=None,
        tls_keyfile=None,
        metrics_db=None,
    )


class _DummyRuntime:
    """Atrapa runtime: podmienia realny ``AgentRuntime``, więc SDK/klucz nie są potrzebne.

    ``run_turn`` NIE powinien być wołany dla komend — sygnalizuje błąd, gdyby jednak był.
    """

    def run_turn(self, *args: object, **kwargs: object) -> object:  # pragma: no cover
        raise AssertionError("runtime nie powinien być wołany dla komendy")


# --- build_read_catalog: zawsze read-only, bez save_note ------------------------


def test_build_read_catalog_returns_four_readonly_tools_without_save_note(tmp_path: Path):
    catalog = build_read_catalog(_settings(tmp_path))

    names = [spec.name for spec in catalog]
    assert names == ["search_notes", "get_note", "list_projects", "get_project_status"]
    assert "save_note" not in names  # bramka ADR 0006: komendy nigdy nie zapisują


def test_build_read_catalog_tools_are_wired_to_real_read_services(tmp_path: Path):
    """Fns katalogu realnie czytają serwisy (nie są puste) — dowód wiringu read-only."""
    catalog = {spec.name: spec.fn for spec in build_read_catalog(_settings(tmp_path))}

    result = catalog["list_projects"]()
    assert result["count"] == 1
    assert result["projects"][0]["key"] == "workmate"


# --- build_conversational_responder: SafeResponder vs goły ----------------------


def _conv_settings(tmp_path: Path) -> ConversationSettings:
    # Kompaktowanie wyłączone → build_compaction_service nie importuje klienta LLM.
    return ConversationSettings(
        db_path=tmp_path / "conv.db", max_context_tokens=1000, compaction_enabled=False
    )


def _build_responder(tmp_path, monkeypatch, *, safe: bool):
    monkeypatch.setattr(
        agent_wiring, "build_agent_runtime_or_exit", lambda *a, **k: _DummyRuntime()
    )
    return build_conversational_responder(
        _settings(tmp_path),
        AgentSettings(),
        _conv_settings(tmp_path),
        channel="telegram",
        enable_write=False,
        safe=safe,
    )


def test_build_conversational_responder_wraps_in_saferesponder_when_safe(
    tmp_path: Path, monkeypatch
):
    responder = _build_responder(tmp_path, monkeypatch, safe=True)
    assert isinstance(responder, SafeResponder)


def test_build_conversational_responder_returns_bare_when_not_safe(tmp_path: Path, monkeypatch):
    responder = _build_responder(tmp_path, monkeypatch, safe=False)
    assert isinstance(responder, ConversationalResponder)


def test_built_responder_dispatches_command_without_calling_runtime(tmp_path: Path, monkeypatch):
    """Złożony responder wykonuje ``/pomoc`` przez router — komenda ≠ tura, runtime niewołany.

    ``_DummyRuntime.run_turn`` rzuciłby, gdyby komenda trafiła do pętli agenta.
    """
    responder = _build_responder(tmp_path, monkeypatch, safe=True)

    reply = asyncio.run(responder.respond(InboundMessage(text="/pomoc", conversation_id="chat1")))
    # ``_help`` (commands.py) poprzedza listę komend krótkim wprowadzeniem "Jestem WorkMate…".
    assert "Dostępne komendy:" in reply


# --- build_agent_runtime_or_exit: brak extra agent → SystemExit -----------------


def test_missing_agent_extra_raises_systemexit_with_hint(tmp_path: Path, monkeypatch):
    """Brak extra ``agent`` (ImportError z build_agent_runtime) → czytelny ``SystemExit``."""

    def _raise(*args: object, **kwargs: object) -> object:
        raise ImportError("No module named 'anthropic'")

    monkeypatch.setattr(agent_wiring, "build_agent_runtime", _raise)

    with pytest.raises(SystemExit) as exc:
        build_agent_runtime_or_exit(_settings(tmp_path), AgentSettings(), enable_write=False)
    assert "extra" in str(exc.value)


def test_scoped_runner_creates_the_conversation_directory(tmp_path):
    """REGRESJA: bez założenia katalogu wykonawca degraduje do korzenia i rozmowy tracą izolację.

    Zmierzone w kontenerze przed poprawką: ``pwd`` w świeżej rozmowie zwracało
    ``/home/scratchpad`` zamiast ``/home/scratchpad/<kanał>/<hash>``, więc pliki jednej rozmowy
    były widoczne dla wszystkich pozostałych.
    """
    from workmate.adapters.inbound.agent_wiring import _ScopedRunner
    from workmate.core.ports.command import CommandResult

    class Spy:
        def __init__(self):
            self.cwd = None

        def run(self, command, *, cwd="", timeout_s=0):
            self.cwd = cwd
            return CommandResult(exit_code=0, stdout="", stderr="")

    spy = Spy()
    target = tmp_path / "teams_graph" / "abc123"

    _ScopedRunner(spy).run("pwd", cwd=str(target))

    assert target.is_dir()
    assert spy.cwd == str(target)


def test_scoped_runner_reports_a_failed_mkdir_as_a_command_result(tmp_path):
    """Nieudane przygotowanie katalogu wraca WYNIKIEM, nie wyjątkiem — tura ma przeżyć."""
    from workmate.adapters.inbound.agent_wiring import _ScopedRunner
    from workmate.core.ports.command import CommandResult

    class Unused:
        def run(self, command, *, cwd="", timeout_s=0):  # pragma: no cover — nie powinno paść
            raise AssertionError("polecenie nie powinno wyjść przy nieudanym mkdir")

    kolizja = tmp_path / "plik"
    kolizja.write_text("nie katalog", encoding="utf-8")

    result = _ScopedRunner(Unused()).run("ls", cwd=str(kolizja / "pod"))

    assert isinstance(result, CommandResult)
    assert result.exit_code == -1
    assert "katalog" in result.stderr.lower()


# --- Powłoka wyklucza narzędzia plikowe (ADR 0009 paczki, krok 5.5) --------------


def _responder_z_katalogiem(tmp_path: Path, monkeypatch, *, powloka: bool):
    """Złóż responder z włączonym katalogiem roboczym i sterowaną obecnością powłoki.

    Fabrykę powłoki podmieniamy, bo prawdziwa zwraca ``None`` na Windows (klient wykonawcy
    jest POSIX-only) — bez podmiany ten test mierzyłby platformę, a nie regułę.
    """
    from workmate.config import ShellSettings, WorkspaceSettings

    monkeypatch.setattr(
        agent_wiring, "build_agent_runtime_or_exit", lambda *a, **k: _DummyRuntime()
    )
    monkeypatch.setattr(
        agent_wiring,
        "_build_shell_factory",
        lambda *a, **k: (lambda scope: []) if powloka else None,
    )
    return build_conversational_responder(
        _settings(tmp_path),
        AgentSettings(),
        _conv_settings(tmp_path),
        channel="teams_graph",
        enable_write=False,
        safe=False,
        enable_workspace=True,
        workspace_settings=WorkspaceSettings(workspace_dir=tmp_path / "ws"),
        shell_settings=ShellSettings(enabled=powloka, socket_path=tmp_path / "exec.sock"),
    )


def test_z_powloka_narzedzia_plikowe_nie_wchodza(tmp_path: Path, monkeypatch):
    """`Bash` startuje w TYM SAMYM katalogu, więc `create_file`/`read_file`/`list_files`
    byłyby opakowaniem prymitywu za trzy pozycje w budżecie wyboru."""
    responder = _responder_z_katalogiem(tmp_path, monkeypatch, powloka=True)
    assert responder._workspace_catalog_factory is None
    assert responder._shell_catalog_factory is not None


def test_bez_powloki_narzedzia_plikowe_zostaja(tmp_path: Path, monkeypatch):
    """Cięcie jest WARUNKOWE, nie bezwarunkowe.

    ``WORKMATE_ENABLE_SHELL`` jest domyślnie wyłączona (ADR 0010 dopuszcza powłokę tylko na
    kanałach z wzajemnie zaufanymi uczestnikami), a bez niej narzędzia plikowe są jedyną drogą,
    którą model odzyskuje własny szkic po kompaktowaniu kontekstu.
    """
    responder = _responder_z_katalogiem(tmp_path, monkeypatch, powloka=False)
    assert responder._workspace_catalog_factory is not None
    assert responder._shell_catalog_factory is None


def _shell_available(tmp_path: Path, monkeypatch, *, chciana: bool, fabryka_daje: bool) -> bool:
    """Zwróć ``shell_available``, z jakim wiring zawołał budowę runtime'u.

    Każde wywołanie dostaje własny korzeń — ``_settings`` zakłada katalog notatek, więc trzy
    układy w jednym ``tmp_path`` przewracałyby się na ``FileExistsError``, a nie na regule.
    """
    from workmate.config import ShellSettings, WorkspaceSettings

    korzen = tmp_path / f"{int(chciana)}{int(fabryka_daje)}"
    korzen.mkdir()
    zebrane: dict[str, object] = {}

    def _runtime(*args, **kwargs):
        zebrane.update(kwargs)
        return _DummyRuntime()

    monkeypatch.setattr(agent_wiring, "build_agent_runtime_or_exit", _runtime)
    monkeypatch.setattr(
        agent_wiring,
        "_build_shell_factory",
        lambda *a, **k: (lambda scope: []) if fabryka_daje else None,
    )
    build_conversational_responder(
        _settings(korzen),
        AgentSettings(),
        _conv_settings(korzen),
        channel="teams_graph",
        enable_write=False,
        safe=False,
        enable_workspace=True,
        workspace_settings=WorkspaceSettings(workspace_dir=korzen / "ws"),
        shell_settings=ShellSettings(enabled=chciana, socket_path=korzen / "exec.sock"),
    )
    return bool(zebrane["shell_available"])


def test_shell_available_bierze_sie_z_FABRYKI_a_nie_z_ustawienia(tmp_path: Path, monkeypatch):
    """Ustawienie mówi, czego chciał operator; fabryka — co agent faktycznie dostanie.

    Rozjazd jest realny: ``_build_shell_factory`` zwraca ``None`` bez ``workspace_settings``
    i na platformie, gdzie klient wykonawcy się nie importuje (POSIX-only). Gdyby flaga szła
    z ustawienia, konfiguracja z ``WORKMATE_ENABLE_SHELL=true`` odebrałaby narzędzia odczytu
    notatek (bo „powłoka je robi") przy nieistniejącej powłoce — agent bez JAKIEJKOLWIEK drogi
    do bazy wiedzy, bez jednego komunikatu.

    Sonda patrzy na argument przekazany do budowy runtime'u, bo to jedyne miejsce, w którym
    ta wartość jest widoczna; asercja na sam katalog narzędzi przepuszczała cofnięcie poprawki.
    """
    assert _shell_available(tmp_path, monkeypatch, chciana=True, fabryka_daje=False) is False
    assert _shell_available(tmp_path, monkeypatch, chciana=True, fabryka_daje=True) is True
    assert _shell_available(tmp_path, monkeypatch, chciana=False, fabryka_daje=False) is False


def test_bez_bramki_katalogu_roboczego_nie_ma_go_nawet_bez_powloki(tmp_path: Path, monkeypatch):
    """Krok 5.5 nie ma prawa WŁĄCZYĆ zdolności tam, gdzie operator jej nie chciał."""
    from workmate.config import ShellSettings, WorkspaceSettings

    monkeypatch.setattr(
        agent_wiring, "build_agent_runtime_or_exit", lambda *a, **k: _DummyRuntime()
    )
    monkeypatch.setattr(agent_wiring, "_build_shell_factory", lambda *a, **k: None)
    responder = build_conversational_responder(
        _settings(tmp_path),
        AgentSettings(),
        _conv_settings(tmp_path),
        channel="teams_graph",
        enable_write=False,
        safe=False,
        enable_workspace=False,
        workspace_settings=WorkspaceSettings(workspace_dir=tmp_path / "ws"),
        shell_settings=ShellSettings(enabled=False, socket_path=tmp_path / "exec.sock"),
    )
    assert responder._workspace_catalog_factory is None


# --- Szew: cwd poleceń a korzeń skrzynki nadawczej (ADR 0009 paczki) -------------


def test_skrzynka_czyta_ten_sam_katalog_w_ktorym_pisze_powloka(tmp_path: Path):
    """Szew między `cwd` polecenia a korzeniem skrzynki — rozjazd wyłącza dostawę bez objawu.

    Obie strony liczą ścieżkę osobno: narzędzie ``Bash`` z ``workspace_root`` i ``scope``,
    a repozytorium skrzynki z ``workspace_settings.workspace_dir`` i ``str(scope.dirpath())``.
    Docstringi obu funkcji ostrzegają przed ich rozjechaniem, ale żaden test ich nie zestawiał:
    każda strona miała pokrycie, szew nie miał żadnego. Objawem rozjazdu jest cisza — model
    zapisuje plik, dostaje kod 0, a załącznik nigdzie nie jedzie.

    Dlatego sonda idzie przez PRODUKCYJNE ``build_shell_catalog`` i ``_build_outbox_delivery``,
    zamiast składać ścieżkę w teście — inaczej sprawdzałaby moje założenie, nie kod.
    """
    from workmate.adapters.inbound.agent_wiring import _build_outbox_delivery, _ScopedRunner
    from workmate.config import WorkspaceSettings
    from workmate.core.application.tools import build_shell_catalog
    from workmate.core.domain.workspace import WorkspaceScope
    from workmate.core.ports.command import CommandResult

    scope = WorkspaceScope(channel="teams_graph", conversation="team/channel/root")
    korzen = tmp_path / "ws"

    class WykonawcaPiszacyDoOutputs:
        """Odwzorowuje `echo … > outputs/raport.md`: zapis WZGLĘDNY wobec otrzymanego ``cwd``."""

        def run(self, command: str, *, cwd: str = "", timeout_s: float = 0) -> CommandResult:
            Path(cwd, "outputs", "raport.md").write_bytes(b"tresc raportu")
            return CommandResult(exit_code=0, stdout="", stderr="")

    wyslane: list[str] = []
    dostawa = _build_outbox_delivery(
        WorkspaceSettings(workspace_dir=korzen),
        lambda conversation: lambda item: wyslane.append(item.name),
        max_file_bytes=1024,
        max_files_per_turn=5,
        max_seconds=10.0,
    )
    powloka = build_shell_catalog(
        scope,
        _ScopedRunner(WykonawcaPiszacyDoOutputs(), with_outbox=True),
        workspace_root=korzen.as_posix(),
        outbox_enabled=True,
    )[0]

    dostawa.snapshot(scope)
    powloka.fn(command="echo tresc raportu > outputs/raport.md")
    komunikat = dostawa.deliver(scope)

    assert wyslane == ["raport.md"], f"plik z powłoki nie dojechał do skrzynki: {komunikat!r}"
