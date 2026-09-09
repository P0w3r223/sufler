"""Testy uwspólnionego wiringu drzwi (``agent_wiring``).

Bez klucza Claude API i bez extra ``agent``: runtime jest podmieniany atrapą przez
monkeypatch, więc żaden import SDK/klienta LLM się nie odpala. Sprawdzamy: katalog
read-only bez ``save_note``, złożenie respondera (``SafeResponder`` vs goły) oraz że
komenda ``/pomoc`` idzie przez router BEZ wołania runtime. Osobno: brak extra ``agent``
→ czytelny ``SystemExit``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from workmate.adapters.inbound import agent_wiring
from workmate.adapters.inbound.agent_wiring import (
    _build_notes_read_factory,
    build_agent_runtime_or_exit,
    build_conversational_responder,
    build_read_catalog,
    file_support,
)
from workmate.adapters.inbound.responder import (
    ConversationalResponder,
    InboundMessage,
    SafeResponder,
)
from workmate.config import AgentSettings, ConversationSettings, Settings
from workmate.core.application.tools import ToolSpec
from workmate.core.domain.workspace import WorkspaceScope
from workmate.core.errors import NoteAuthorizationError
from workmate.core.ports.llm import AttachmentQueue
from workmate.core.ports.materialization import MaterializationLimits

# Sondy, które budują PRAWDZIWĄ fabrykę powłoki, dotykają klienta wykonawcy — a ten jest
# POSIX-only (gniazda unix) i na Windows podnosi ``ImportError`` w ciele modułu. Bramką jest
# platforma, nie import: od pytest 9.1 ``importorskip`` domyślnie zamienia na skip wyłącznie
# ``ModuleNotFoundError``, więc wyjątek podniesiony WEWNĄTRZ istniejącego modułu leci dalej jako
# porażka. Warunek jest tu dosłownie tym samym, co strażnik produkcyjny
# (``exec_client.py``: ``if sys.platform == "win32": raise ImportError``), więc na Linuksie
# (obraz floty, CI) obie sondy biegną w pełni.
posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="klient wykonawcy jest POSIX-only (gniazda unix)"
)

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
        audit_db=None,
        note_snapshots_dir=tmp_path / "snapshots",
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


# --- _build_notes_read_factory: bramka odczytu per turę (ADR 0062) ---------------


class _StubReadAuthz:
    """Atrapa authorizera odczytu: przepuszcza znane AAD id, resztę odrzuca (fail-closed)."""

    def __init__(self, allowed: set[str]) -> None:
        self._allowed = allowed

    def authorize(self, requester_aad_id: str) -> None:
        if requester_aad_id not in self._allowed:
            raise NoteAuthorizationError("nierozpoznany nadawca (stub, ADR 0062)")

    def trust_class(self, requester_aad_id: str) -> str:
        """Rozszczepienie T1/T2 (ADR 0066) jedzie za tą samą bramką i z tego samego rozwiązania
        tożsamości (R4), więc atrapa musi umieć jedno i drugie — inaczej sonda powierzchni
        wywraca się na wiringu, a nie na regule, którą mierzy."""
        return "T1" if requester_aad_id in self._allowed else "T2"


def test_notes_read_factory_recognized_member_gets_real_tools(tmp_path: Path):
    factory = _build_notes_read_factory(_settings(tmp_path), _StubReadAuthz({"aad-ok"}))

    tools = factory("aad-ok")

    assert [t.name for t in tools] == ["Project", "SearchNotes", "GetNote", "ListProjects"]
    by_name = {t.name: t.fn for t in tools}
    result = by_name["ListProjects"]()
    assert result["count"] == 1  # realny serwis, nie odmowa


def test_notes_read_factory_unrecognized_sender_gets_refusals(tmp_path: Path):
    factory = _build_notes_read_factory(_settings(tmp_path), _StubReadAuthz(set()))

    tools = factory("aad-obcy")

    # Te SAME nazwy (schemat zachowany przez functools.wraps), ale fn zwraca odmowę.
    assert [t.name for t in tools] == ["Project", "SearchNotes", "GetNote", "ListProjects"]
    by_name = {t.name: t.fn for t in tools}
    # Wołanie z realnym kwargiem nie wybucha (zachowana sygnatura) i zwraca odmowę.
    search_out = by_name["SearchNotes"](query="scada")
    assert "Brak uprawnień do odczytu bazy wiedzy" in search_out["error"]
    assert "Brak uprawnień do odczytu bazy wiedzy" in by_name["ListProjects"]()["error"]


def test_project_status_is_gated_like_the_rest_of_the_read_surface(tmp_path: Path):
    """Regresja ADR 0062: ``Project(action='status')`` był JEDYNĄ drogą odczytu bazy
    wiedzy, która przeżyła wpięcie bramki — ``suppress_notes_read`` zdejmował tylko trójkę
    ``build_agent_notes_read_catalog``, a ``Project`` z katalogu bazowego zostawał zawsze.
    """
    factory = _build_notes_read_factory(_settings(tmp_path), _StubReadAuthz(set()))

    notes = next(t for t in factory("aad-obcy") if t.name == "Project")

    out = notes.fn(action="status", project="workmate")
    assert "Brak uprawnień do odczytu bazy wiedzy" in out["error"]


def test_build_project_catalog_zwraca_sam_Project(tmp_path: Path):
    """Builder bazowy oferuje DOKŁADNIE ``Project`` — to właśnie znika przy ``suppress_notes_read``.

    **Przemianowana 2026-09-09 na to, co naprawdę mierzy.** Nazywała się dotąd
    ``…_offers_no_knowledge_base_tool_when_the_gate_owns_them`` i obiecywała w docstringu, że
    pilnuje, by katalog BAZOWY nie oferował niczego z bazy wiedzy, „bo gdyby ``Project`` w nim
    został, per-turowa odmowa byłaby dekoracją obok czynnego narzędzia o tej samej nazwie".
    Ciało nie dotykało ani ``suppress_notes_read``, ani wiringu — wołało builder wprost, więc
    asercja była wręcz ODWROTNA do nazwy i przeszłaby przy bramce zepsutej do zera. Sonda stała
    najbliżej wady U1, opisywała ją co do słowa i jej nie widziała.

    Obietnicę z tamtego docstringa egzekwuje dziś
    ``test_bramka_odczytu_nie_wygasa_przy_wlaczonej_powloce`` — na zmontowanej powierzchni.
    """
    from workmate.adapters.outbound.markdown_notes_repo import MarkdownNotesRepository
    from workmate.adapters.outbound.yaml_projects_repo import YamlProjectsRepository
    from workmate.core.application.services import ProjectsService
    from workmate.core.application.tools import build_project_catalog

    settings = _settings(tmp_path)
    projects = ProjectsService(
        YamlProjectsRepository(settings.projects_registry),
        MarkdownNotesRepository(settings.notes_dir),
    )
    bazowe = build_project_catalog(projects)

    assert [t.name for t in bazowe] == ["Project"]  # to właśnie znika przy suppress_notes_read


def test_fabryka_zachowuje_akcje_save_dla_drzwi_zaufanych(tmp_path: Path):
    """Docstring fabryki obiecuje, że ``enable_write`` „inaczej cicho zabrałoby drzwiom zaufanym
    akcję ``save``" — a nic tego nie mierzyło.

    Macierz właśnie urosła do czterech kombinacji (``enable_write`` × ``shell_available``),
    z czego sondy pokrywały dwie, obie z ``enable_write=False``. Obietnica bez sondy jest w tym
    repozytorium klasą wady, nie stylem.
    """
    factory = _build_notes_read_factory(
        _settings(tmp_path), _StubReadAuthz({"aad-ok"}), enable_write=True, shell_available=True
    )
    projekt = next(t for t in factory("aad-ok") if t.name == "Project")
    assert "save" in projekt.description, "wariant rw zniknął — drzwi zaufane straciły zapis"

    korzen_ro = tmp_path / "ro"
    korzen_ro.mkdir()
    bez_zapisu = _build_notes_read_factory(
        _settings(korzen_ro), _StubReadAuthz({"aad-ok"}), shell_available=True
    )
    assert "save" not in next(t for t in bez_zapisu("aad-ok") if t.name == "Project").description


def test_z_powloka_fabryka_niesie_sam_Project_bez_trojki_odczytu(tmp_path: Path):
    """Z powłoką trójka odczytu jest zbędna, ale ``Project`` MUSI zostać za bramką.

    To on serwuje treść (``action='status'`` zwraca syntezę z notatek pionu), więc zdjęcie go
    razem z trójką otworzyłoby drogę obok bramki — dokładnie tę, którą ADR 0062 zamykał.
    """
    factory = _build_notes_read_factory(
        _settings(tmp_path), _StubReadAuthz({"aad-ok"}), shell_available=True
    )

    assert [t.name for t in factory("aad-ok")] == ["Project"]

    odmowa = next(t for t in factory("aad-obcy") if t.name == "Project")
    out = odmowa.fn(action="status", project="workmate")
    assert "Brak uprawnień do odczytu bazy wiedzy" in out["error"]


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


_PLIKOWE = {"CreateFile", "ReadFile", "ListFiles"}


def test_z_powloka_narzedzia_plikowe_nie_wchodza(tmp_path: Path, monkeypatch):
    """`Bash` startuje w TYM SAMYM katalogu, więc `create_file`/`read_file`/`list_files`
    byłyby opakowaniem prymitywu za trzy pozycje w budżecie wyboru.

    Sonda mierzy ZMONTOWANĄ powierzchnię tury (co model naprawdę dostał), nie obecność fabryki
    w responderze: fabryka zwracająca pustą listę przechodziła asercję na atrybut, a agentowi
    nie dawała niczego — i odwrotnie, samo pole ``None`` nie dowodzi, że narzędzia nie weszły
    inną drogą (katalog bazowy, ``extra_catalog``).
    """
    nazwy = _zmontowana_powierzchnia(tmp_path, monkeypatch, powloka=True, file_reply=False)

    assert "Bash" in nazwy
    assert _PLIKOWE.isdisjoint(nazwy)


def test_bez_powloki_narzedzia_plikowe_zostaja(tmp_path: Path, monkeypatch):
    """Cięcie jest WARUNKOWE, nie bezwarunkowe.

    ``WORKMATE_ENABLE_SHELL`` jest domyślnie wyłączona (ADR 0010 dopuszcza powłokę tylko na
    kanałach z wzajemnie zaufanymi uczestnikami), a bez niej narzędzia plikowe są jedyną drogą,
    którą model odzyskuje własny szkic po kompaktowaniu kontekstu.
    """
    nazwy = _zmontowana_powierzchnia(tmp_path, monkeypatch, powloka=False, file_reply=False)

    assert set(nazwy) >= _PLIKOWE
    assert "Bash" not in nazwy


# --- Bramka członkostwa powłoki (ADR 0063) — fabryka omija narzędzie nierozpoznanemu nadawcy ---


class _FakeLookup:
    """Atrapa ``AadIdentityLookup`` — zna wskazane AAD id, resztę zwraca ``None`` (fail-closed)."""

    def __init__(self, people: dict) -> None:
        self._people = people

    def resolve_by_aad_user_id(self, aad_user_id: str):
        return self._people.get(aad_user_id)


def _shell_factory_with(authorizer, tmp_path: Path):
    """Prawdziwa fabryka powłoki z podanym autoryzatorem (klient wykonawcy jest POSIX-only)."""
    from workmate.config import ShellSettings, WorkspaceSettings

    return agent_wiring._build_shell_factory(
        ShellSettings(enabled=True, manager_socket_path=tmp_path / "control.sock"),
        WorkspaceSettings(workspace_dir=tmp_path / "ws"),
        authorizer=authorizer,
    )


@posix_only
def test_powloka_bramkowana_czlonkostwem(tmp_path: Path):
    """§1 ADR 0063: z autoryzatorem członek dostaje powłokę, obcy/bez-tożsamości — pustą listę."""
    from workmate.core.application.shell_authz import ShellAuthorizer
    from workmate.core.domain.identity import Person
    from workmate.core.domain.workspace import WorkspaceScope

    anna = Person(
        source_id="EMP-1", aad_user_id="aad-anna", jira_user="a@example.org", display_name="Anna"
    )
    factory = _shell_factory_with(ShellAuthorizer(_FakeLookup({"aad-anna": anna})), tmp_path)
    assert factory is not None
    scope = WorkspaceScope("teams_graph", "team/kanal/watek")

    assert factory(scope, "aad-anna")  # rozpoznany członek → powłoka obecna (niepusta lista)
    assert factory(scope, "aad-obcy") == []  # nie-członek → pominięta (build-time omission)
    assert factory(scope, "") == []  # brak tożsamości nadawcy → pominięta (fail-closed)


@posix_only
def test_powloka_bez_autoryzatora_nie_bramkuje(tmp_path: Path):
    """Drzwi zaufane (CLI): ``authorizer=None`` → powłoka jak przed ADR 0063, bez bramki nadawcy."""
    from workmate.core.domain.workspace import WorkspaceScope

    factory = _shell_factory_with(None, tmp_path)
    assert factory is not None
    scope = WorkspaceScope("teams_graph", "team/kanal/watek")

    assert factory(scope, "")  # brak sender_id i brak autoryzatora → powłoka obecna


# --- Powłoka wyklucza reply_with_file — szóste narzędzie (etap 7, ADR 0011 paczki) ---


def _responder_z_reply_file(tmp_path: Path, monkeypatch, *, powloka: bool):
    """Responder z ``thread_tool_factory`` (fabryka ``ReplyWithFile``) i sterowaną powłoką.

    Fabrykę powłoki podmieniamy, bo prawdziwa zwraca ``None`` na Windows (klient wykonawcy
    jest POSIX-only) — bez podmiany ta sonda mierzyłaby platformę, a nie regułę.
    """
    from workmate.config import ShellSettings, WorkspaceSettings

    monkeypatch.setattr(
        agent_wiring, "build_agent_runtime_or_exit", lambda *a, **k: _DummyRuntime()
    )
    monkeypatch.setattr(
        agent_wiring,
        "_build_shell_factory",
        lambda *a, **k: (lambda scope, sender_id: []) if powloka else None,
    )
    return build_conversational_responder(
        _settings(tmp_path),
        AgentSettings(),
        _conv_settings(tmp_path),
        channel="teams_graph",
        enable_write=False,
        safe=False,
        workspace_settings=WorkspaceSettings(workspace_dir=tmp_path / "ws"),
        shell_settings=ShellSettings(
            enabled=powloka, manager_socket_path=tmp_path / "control.sock"
        ),
        thread_tool_factory=lambda external_id: [],
    )


def test_z_powloka_reply_with_file_schodzi_z_powierzchni(tmp_path: Path, monkeypatch):
    """Etap 7: z powłoką dostawa idzie skrzynką ``outputs/``, więc ``ReplyWithFile`` — szóste
    narzędzie — nie wchodzi (byłoby DRUGĄ drogą do tej samej zdolności)."""
    responder = _responder_z_reply_file(tmp_path, monkeypatch, powloka=True)
    assert responder._thread_tool_factory is None


def test_bez_powloki_reply_with_file_zostaje(tmp_path: Path, monkeypatch):
    """Cięcie WARUNKOWE: bez powłoki ``ReplyWithFile`` jest JEDYNĄ drogą dostawy pliku.

    Zostaje.
    """
    responder = _responder_z_reply_file(tmp_path, monkeypatch, powloka=False)
    assert responder._thread_tool_factory is not None


# --- 7.3: golden ZMONTOWANEJ powierzchni — układ docelowy zamrożony na piątce (etap 7) ---


class _RecordingLLM:
    """Atrapa klienta LLM: zapisuje nazwy narzędzi z JEDNEGO wywołania ``complete`` i kończy turę.

    ``tool_calls`` puste → ``run_turn`` nie dispatchuje i wraca po pierwszej iteracji, więc
    ``tool_names`` niesie DOKŁADNIE tę powierzchnię, którą model dostał w tej turze.
    """

    def __init__(self, *_a: object, **_k: object) -> None:
        self.tool_names: list[str] = []
        self.tools: list = []

    def complete(self, *, system, transcript, tools, trust_nonce=""):  # noqa: ANN001, ANN201
        from workmate.core.domain.pricing import TokenUsage
        from workmate.core.ports.llm import LLMResponse

        self.tools = list(tools)
        self.tool_names = [spec.name for spec in tools]
        return LLMResponse(text="ok", stop_reason="end_turn", usage=TokenUsage())


def _zmontowana_powierzchnia(tmp_path, monkeypatch, **kwargs) -> list[str]:
    """Same NAZWY — dla sond, które pytają o skład powierzchni."""
    return [spec.name for spec in _zmontowany_katalog(tmp_path, monkeypatch, **kwargs)]


def _zmontowany_katalog(
    tmp_path, monkeypatch, *, powloka: bool, file_reply: bool, authorizer=None
) -> list[ToolSpec]:
    """Zwróć SPECYFIKACJE narzędzi, jakie model dostaje w turze z realnego respondera.

    GitHub/Jira/Schedule wchodzą jako statyczne STUBY drzwi (ADR 0019/0020) — ich wnętrze ma
    własne testy; tu mierzymy SKŁADANIE powierzchni i bramkę etapu 7, nie ich budowniki. ``Notes``
    i bramka ``ReplyWithFile`` idą przez PRAWDZIWY kod (``build_agent_runtime`` + gating 7.1).
    """
    from workmate.config import ShellSettings, WorkspaceSettings

    def _stub(name: str) -> ToolSpec:
        return ToolSpec(name, "", lambda **_kw: {})

    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    recording = _RecordingLLM()
    monkeypatch.setattr(
        "workmate.adapters.outbound.anthropic_llm.AnthropicLLMClient",
        lambda *a, **k: recording,
    )
    monkeypatch.setattr(
        agent_wiring,
        "_build_shell_factory",
        lambda *a, **k: (lambda scope, sender_id: [_stub("Bash")]) if powloka else None,
    )
    responder = build_conversational_responder(
        _settings(tmp_path),
        AgentSettings(),
        _conv_settings(tmp_path),
        channel="teams_graph",
        enable_write=False,
        safe=False,
        enable_workspace=True,
        workspace_settings=WorkspaceSettings(workspace_dir=tmp_path / "ws"),
        shell_settings=ShellSettings(
            enabled=powloka, manager_socket_path=tmp_path / "control.sock"
        ),
        extra_catalog=[_stub("Activity"), _stub("Schedule")],
        my_jira_tasks_factory=lambda sender: [_stub("Jira")],
        thread_tool_factory=(lambda ext: [_stub("ReplyWithFile")]) if file_reply else None,
        note_read_authorizer=authorizer,
    )
    asyncio.run(responder.respond(InboundMessage(text="q", conversation_id="c", sender_id="u-1")))
    return recording.tools


def test_uklad_docelowy_zamrozony_na_piatce_bez_szostego_narzedzia(tmp_path: Path, monkeypatch):
    """Etap 7: układ z powłoką + dostawą pliku (dawny C=6) montuje DOKŁADNIE pięć narzędzi
    docelowych i NIE zawiera ``ReplyWithFile`` — dostawa zeszła do skrzynki ``outputs/``.

    Golden: nowe narzędzie w powierzchni ZERWIE tę sondę, zanim wejdzie niezauważone — tabela
    układów A–D nie miała dotąd bramki (przebudowa-harnessu §7.3).
    """
    nazwy = _zmontowana_powierzchnia(tmp_path, monkeypatch, powloka=True, file_reply=True)
    # `sorted`, nie `set`: zbiór pięcioelementowy powstaje równie dobrze z SZEŚCIU pozycji, więc
    # `set()` wymazywał DUPLIKAT — a dwa `Project` w powierzchni (bramkowany obok niebramkowanego)
    # to dokładnie objaw rodziny wad, którą ten golden ma zamrażać.
    assert sorted(nazwy) == ["Activity", "Bash", "Jira", "Project", "Schedule"]
    assert "ReplyWithFile" not in nazwy


def test_bramka_odczytu_nie_wygasa_przy_wlaczonej_powloce(tmp_path: Path, monkeypatch):
    """Regresja U1, mierzona na ZMONTOWANEJ POWIERZCHNI — tej, którą dostaje model.

    Przed poprawką warunek bramki brzmiał ``authorizer is not None and shell_factory is None``,
    z uzasadnieniem „z powłoką narzędzi odczytu i tak nie ma". Dla trójki to prawda, dla
    ``Project`` nie: ``build_project_catalog`` nie zależy od ``shell_available`` wcale, więc
    przy ``ENABLE_SHELL=true`` ``Project`` zostawał w katalogu BAZOWYM — poza bramką, i po cichu.
    Bramka wygasała dokładnie w układzie docelowym.

    **Sonda WOŁA narzędzie, zamiast je liczyć, i to jest jej treść.** Pierwsza redakcja pytała
    o ``count("Project") == 1`` — a to przechodzi także dla stanu SPRZED poprawki, gdzie
    jedyny ``Project`` w powierzchni jest tym spoza bramki. Liczba narzędzi nie odróżnia
    bramkowanego od niebramkowanego; odróżnia je dopiero odpowiedź dla obcego nadawcy.
    """
    katalog = _zmontowany_katalog(
        tmp_path / "obcy",
        monkeypatch,
        powloka=True,
        file_reply=False,
        authorizer=_StubReadAuthz(set()),  # nadawca `u-1` NIE jest zmapowany
    )
    nazwy = [spec.name for spec in katalog]
    projekty = [spec for spec in katalog if spec.name == "Project"]
    assert len(projekty) == 1, f"drugi ``Project`` obok bramki: {nazwy}"

    out = projekty[0].fn(action="status", project="workmate")
    assert "Brak uprawnień do odczytu bazy wiedzy" in out.get("error", ""), (
        "``Project`` w powierzchni modelu NIE jest tym zza bramki — czyli bramka odczytu "
        f"wygasła przy włączonej powłoce. Powierzchnia: {nazwy}"
    )
    assert "Bash" in nazwy  # powłoka nietknięta — bramka dotyczy powierzchni notatek


def test_bramka_odczytu_z_powloka_daje_zmapowanemu_dzialajacy_Project(tmp_path: Path, monkeypatch):
    """Druga połowa: bramka ma PRZEPUSZCZAĆ, a nie tylko odmawiać.

    Bez tej sondy poprzednią spełniałby też kod, który zawsze odmawia — a to nie jest bramka,
    tylko awaria wyglądająca na bezpieczeństwo.
    """
    katalog = _zmontowany_katalog(
        tmp_path / "zmapowany",
        monkeypatch,
        powloka=True,
        file_reply=False,
        authorizer=_StubReadAuthz({"u-1"}),
    )
    projekt = next(spec for spec in katalog if spec.name == "Project")
    out = projekt.fn(action="status", project="workmate")
    assert "Brak uprawnień" not in str(out.get("error", ""))


def test_bez_powloki_reply_with_file_jest_w_zmontowanej_powierzchni(tmp_path: Path, monkeypatch):
    """Dopełnienie: bez powłoki dostawy nie ma czym zastąpić, więc ``ReplyWithFile`` JEST
    w zmontowanej powierzchni (a narzędzia odczytu notatek wchodzą zastępczo)."""
    nazwy = _zmontowana_powierzchnia(tmp_path, monkeypatch, powloka=False, file_reply=True)
    assert "ReplyWithFile" in nazwy


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
        lambda *a, **k: (lambda scope, sender_id: []) if fabryka_daje else None,
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
        shell_settings=ShellSettings(enabled=chciana, manager_socket_path=korzen / "control.sock"),
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


# --- Etap 6: opis świata idzie z tej samej fabryki co katalog narzędzi ----------


def _drzwi_z_powloka(tmp_path: Path, monkeypatch, *, fabryka_daje: bool, skills: Path | None):
    """Złóż drzwi Teams i zwróć (kwargi budowy runtime'u, responder).

    Fabrykę powłoki podmieniamy z tego samego powodu co wyżej: prawdziwa zwraca ``None``
    na Windows, więc bez podmiany sonda mierzyłaby platformę zamiast reguły.
    """
    from workmate.config import ShellSettings, SkillsSettings, WorkspaceSettings

    korzen = tmp_path / f"{int(fabryka_daje)}{int(skills is not None)}"
    korzen.mkdir()
    zebrane: dict[str, object] = {}

    def _runtime(*args, **kwargs):
        zebrane.update(kwargs)
        return _DummyRuntime()

    monkeypatch.setattr(agent_wiring, "build_agent_runtime_or_exit", _runtime)
    monkeypatch.setattr(
        agent_wiring,
        "_build_shell_factory",
        lambda *a, **k: (lambda scope, sender_id: []) if fabryka_daje else None,
    )
    responder = build_conversational_responder(
        _settings(korzen),
        AgentSettings(),
        _conv_settings(korzen),
        channel="teams_graph",
        enable_write=False,
        safe=False,
        enable_workspace=True,
        workspace_settings=WorkspaceSettings(workspace_dir=korzen / "ws"),
        shell_settings=ShellSettings(enabled=True, manager_socket_path=korzen / "control.sock"),
        skills_settings=None if skills is None else SkillsSettings(skills_dir=skills),
    )
    return zebrane, responder


def _katalog_procedur(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    (root / "zestawienie").mkdir(parents=True)
    (root / "zestawienie" / "SKILL.md").write_text(
        "# Zestawienie\n\nSkłada zestawienie do wysłania.\n", encoding="utf-8"
    )
    return root


def test_korpus_opisuje_montaze_dokladnie_wtedy_gdy_powloka_istnieje(tmp_path: Path, monkeypatch):
    """Wariant ``ENVIRONMENT`` bierze się z FABRYKI, tak samo jak ``shell_available``.

    Rozjazd tych dwóch jest defektem, który etap 6 zamyka: agent czytałby „the knowledge base
    lives behind tools" przy katalogu, z którego te narzędzia właśnie usunięto — albo odwrotnie,
    dostałby mapę montaży bez powłoki, którą mógłby po niej chodzić.
    """
    z_powloka, _ = _drzwi_z_powloka(tmp_path, monkeypatch, fabryka_daje=True, skills=None)
    bez_powloki, _ = _drzwi_z_powloka(tmp_path, monkeypatch, fabryka_daje=False, skills=None)

    assert z_powloka["shell_available"] is True
    assert "/mnt/system/notes/" in str(z_powloka["system_prompt"])
    assert "lives behind tools" not in str(z_powloka["system_prompt"])

    assert bez_powloki["shell_available"] is False
    assert "lives behind tools" in str(bez_powloki["system_prompt"])
    assert "/mnt/system/notes/" not in str(bez_powloki["system_prompt"])


def test_lista_procedur_wchodzi_do_naglowka_dopiero_z_powloka(tmp_path: Path, monkeypatch):
    """Martwa obietnica tej samej klasy co ``/mnt/user/outputs`` — złapana w etapie 6.

    Nagłówek sesji mówi „read the one that fits before starting", a jedyną drogą do TREŚCI
    procedury jest ``cat`` w wykonawcy: narzędzia plikowe katalogu roboczego są domknięte
    w scope'ie rozmowy i ``/mnt/skills`` nie widzą. Bez powłoki model dostawał więc listę nazw
    i polecenie przeczytania czegoś, po co nie ma jak sięgnąć.
    """
    procedury = _katalog_procedur(tmp_path)

    _, z_powloka = _drzwi_z_powloka(tmp_path, monkeypatch, fabryka_daje=True, skills=procedury)
    _, bez_powloki = _drzwi_z_powloka(tmp_path, monkeypatch, fabryka_daje=False, skills=procedury)

    assert z_powloka._skills == (("zestawienie", "Składa zestawienie do wysłania."),)
    assert bez_powloki._skills == ()


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
        shell_settings=ShellSettings(enabled=False, manager_socket_path=tmp_path / "control.sock"),
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

    # Skrzynka dokleja do nazwy skrót TREŚCI (``raport-<8 hex>.md``), żeby plik z jednego wątku
    # nie nadpisał pliku z drugiego na wspólnym dysku kanału. Ta sonda jest o SZWIE powłoka →
    # skrzynka, więc porównuje po zdjęciu sufiksu; regułę nazewnictwa zamraża
    # ``tests/core/test_outbox_delivery.py``.
    import re

    bez_skrotu = [re.sub(r"-[0-9a-f]{8}(?=\.[^.]+$)", "", n) for n in wyslane]
    assert bez_skrotu == ["raport.md"], f"plik z powłoki nie dojechał do skrzynki: {komunikat!r}"


# --- ``File`` i odkładanie załączników (ADR 0064) -------------------------------

_PULAPY = MaterializationLimits(max_bytes=10_000_000, max_extract_bytes=10_000_000)


def _workspace_settings(tmp_path: Path):
    from workmate.config import WorkspaceSettings

    return WorkspaceSettings(
        enabled=True,
        workspace_dir=tmp_path / "scratchpad",
        max_file_mb=5,
        max_files_per_scope=20,
        max_total_mb=20,
        allowed_ext=("md", "txt", "csv", "json"),
    )


def test_stager_writes_the_users_file_to_the_conversation_directory(tmp_path: Path):
    """Sedno: dotąd załącznik żył WYŁĄCZNIE w blokach rozmowy, na wolumenie, którego wykonawca
    nie montuje — więc ani powłoka, ani ``File(read)`` nie miały czego czytać."""
    from workmate.core.domain.workspace import WorkspaceScope
    from workmate.core.ports.llm import Attachment

    _factory, stage = agent_wiring.build_file_support(
        _workspace_settings(tmp_path),
        max_image_edge=2048,
        staged_ext=frozenset({"pdf", "md", "txt"}),
        materialization_limits=_PULAPY,
    )
    scope = WorkspaceScope("teams_graph", "team/chan/root")

    names = stage(scope, (Attachment("document", "application/pdf", "umowa.pdf", "QkFTRTY0"),))

    assert names == ["umowa.pdf"]
    zapisany = (tmp_path / "scratchpad" / scope.dirpath() / "umowa.pdf").read_bytes()
    assert zapisany == b"BASE64"  # bajty ODKODOWANE, nie base64 jako tekst


def test_stager_skips_the_status_note_that_stands_in_for_a_missing_file(tmp_path: Path):
    """Notka „nie udało się pobrać" to KOMUNIKAT, nie plik — zapisanie jej pod nazwą pliku
    dałoby model, który czyta własny błąd i bierze go za treść dokumentu."""
    from workmate.core.domain.workspace import WorkspaceScope
    from workmate.core.ports.llm import Attachment

    _factory, stage = agent_wiring.build_file_support(
        _workspace_settings(tmp_path),
        max_image_edge=2048,
        staged_ext=frozenset({"pdf", "txt"}),
        materialization_limits=_PULAPY,
    )

    names = stage(
        WorkspaceScope("teams_graph", "t/c/r"),
        (Attachment("text", "text/plain", "status załącznika", text="nie udało się pobrać"),),
    )

    assert names == []


def test_stager_skips_a_file_it_cannot_place_without_killing_the_rest(tmp_path: Path):
    """Jeden plik nie do odłożenia (rozszerzenie spoza listy) nie może zabrać pozostałych."""
    from workmate.core.domain.workspace import WorkspaceScope
    from workmate.core.ports.llm import Attachment

    _factory, stage = agent_wiring.build_file_support(
        _workspace_settings(tmp_path),
        max_image_edge=2048,
        staged_ext=frozenset({"pdf", "txt"}),
        materialization_limits=_PULAPY,
    )

    names = stage(
        WorkspaceScope("teams_graph", "t/c/r"),
        (
            Attachment("image", "image/png", "zrzut.exe", "QkFTRTY0"),
            Attachment("document", "application/pdf", "umowa.pdf", "QkFTRTY0"),
        ),
    )

    assert names == ["umowa.pdf"]


def test_file_tool_reads_back_exactly_what_the_stager_wrote(tmp_path: Path):
    """Pętla domknięta: drzwi odkładają plik, model prosi o niego ``File(read)`` i go dostaje."""
    from workmate.core.domain.workspace import WorkspaceScope
    from workmate.core.ports.llm import Attachment, AttachmentQueue

    factory, stage = agent_wiring.build_file_support(
        _workspace_settings(tmp_path),
        max_image_edge=2048,
        staged_ext=frozenset({"pdf", "txt"}),
        materialization_limits=_PULAPY,
    )
    scope = WorkspaceScope("teams_graph", "team/chan/root")
    (nazwa,) = stage(scope, (Attachment("document", "application/pdf", "umowa.pdf", "JVBERi0x"),))

    queue = AttachmentQueue(budget_bytes=1_000_000)
    (spec,) = factory(scope, queue, "", "tura-1")
    result = spec.fn(action="read", name=nazwa)

    assert result["materialized"] is True
    (podany,) = queue.drain()
    assert (podany.kind, podany.media_type) == ("document", "application/pdf")


def test_extracted_document_is_staged_under_a_name_that_does_not_lie(tmp_path: Path):
    """Odłożony ``.docx`` zawiera TEKST po ekstrakcji — pod nazwą ``.docx`` byłby pułapką.

    Drzwi materializują Worda jako tekst (API nie przyjmuje go natywnie), więc oryginalnych
    bajtów już nie ma. Zapisany pod ``raport.docx`` plik kłamałby rozszerzeniem: ``File(read)``
    rozpoznałby ``.docx`` i puścił na niego czytnik Worda, który przewraca się na „to nie jest
    zip" — a tak samo `workmate-extract` w powłoce.
    """
    from workmate.core.domain.workspace import WorkspaceScope
    from workmate.core.ports.llm import Attachment

    _factory, stage = agent_wiring.build_file_support(
        _workspace_settings(tmp_path),
        max_image_edge=2048,
        staged_ext=frozenset({"pdf", "txt"}),
        materialization_limits=_PULAPY,
    )
    scope = WorkspaceScope("teams_graph", "t/c/r")

    names = stage(
        scope, (Attachment("text", "text/plain", "raport.docx", text="Treść umowy po ekstrakcji"),)
    )

    assert names == ["raport.txt"]
    zapisany = tmp_path / "scratchpad" / scope.dirpath() / "raport.txt"
    assert zapisany.read_text(encoding="utf-8") == "Treść umowy po ekstrakcji"


def test_staged_document_can_actually_be_read_back_by_the_tool(tmp_path: Path):
    """Pętla domknięta dla dokumentu: co drzwi odłożyły, to model musi umieć odczytać."""
    from workmate.core.domain.workspace import WorkspaceScope
    from workmate.core.ports.llm import Attachment, AttachmentQueue

    factory, stage = agent_wiring.build_file_support(
        _workspace_settings(tmp_path),
        max_image_edge=2048,
        staged_ext=frozenset({"pdf", "txt"}),
        materialization_limits=_PULAPY,
    )
    scope = WorkspaceScope("teams_graph", "t/c/r")
    (nazwa,) = stage(scope, (Attachment("text", "text/plain", "raport.docx", text="Treść umowy"),))

    queue = AttachmentQueue(budget_bytes=1_000_000)
    (spec,) = factory(scope, queue, "", "tura-1")
    result = spec.fn(action="read", name=nazwa)

    assert result["materialized"] is True  # nie „nie jest zipem"
    (podany,) = queue.drain()
    assert podany.text == "Treść umowy"


def _responder_z_file(tmp_path: Path, monkeypatch, *, wlaczony: bool):
    """Responder z bramką ``File`` w zadanym stanie (reszta jak w sondach powłoki)."""
    from workmate.config import WorkspaceSettings

    monkeypatch.setattr(
        agent_wiring, "build_agent_runtime_or_exit", lambda *a, **k: _DummyRuntime()
    )
    monkeypatch.setattr(agent_wiring, "_build_shell_factory", lambda *a, **k: None)
    return build_conversational_responder(
        _settings(tmp_path),
        AgentSettings(),
        _conv_settings(tmp_path),
        channel="teams_graph",
        enable_write=False,
        safe=False,
        enable_workspace=True,
        workspace_settings=WorkspaceSettings(enabled=True, workspace_dir=tmp_path / "ws"),
        supports_attachments=True,
        enable_file_tool=wlaczony,
        file_tool_budget_bytes=1_000_000,
        file_tool_staged_ext=frozenset({"pdf"}),
        file_tool_limits=_PULAPY,
    )


def test_file_tool_gate_off_means_no_tool_and_no_staging(tmp_path: Path, monkeypatch):
    """Bramka ma naprawdę gasić OBIE strony — samo narzędzie i zapis cudzego pliku na dysk.

    Bramka, która wyłącza narzędzie, ale zostawia odkładanie plików, wyglądałaby jak wyłączona,
    a dalej zapisywałaby załączniki użytkownika na dysk floty.
    """
    responder = _responder_z_file(tmp_path, monkeypatch, wlaczony=False)

    assert responder._file_catalog_factory is None
    assert responder._attachment_stager is None


def test_file_tool_gate_on_wires_both_sides(tmp_path: Path, monkeypatch):
    responder = _responder_z_file(tmp_path, monkeypatch, wlaczony=True)

    assert responder._file_catalog_factory is not None
    assert responder._attachment_stager is not None


def test_the_file_factory_forwards_the_verdict_sink_to_the_catalog(tmp_path: Path, monkeypatch):
    """Jedyne ogniwo łańcucha werdyktu (ADR 0065 §8), które da się urwać niezauważenie.

    ``build_file_support`` podaje argumenty do ``build_file_catalog`` POZYCYJNIE, więc dołożenie
    czegokolwiek przed ``verdict_sink`` przesunie go na cudze miejsce. Reszta trasy — responder →
    fabryka i fabryka → ujście — ma własne sondy; ten kawałek nie miał żadnej.
    """
    widziane: dict[str, object] = {}

    def szpieg(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        widziane["ostatni"] = args[-1]
        return []

    # Podmiana MUSI trafić w moduł, w którym ``build_file_support`` szuka tej nazwy
    # (``agent_wiring.file_support``), a nie w pakiet — po rozbiciu `agent_wiring` na pakiet
    # podmiana na nim byłaby CICHO bez efektu i sonda przechodziłaby na niepodmienionym kodzie.
    monkeypatch.setattr(file_support, "build_file_catalog", szpieg)
    responder = _responder_z_file(tmp_path, monkeypatch, wlaczony=True)
    assert responder._file_catalog_factory is not None

    def ujscie(_verdict: str, _reason: str) -> None:
        return None

    responder._file_catalog_factory(
        WorkspaceScope("teams_graph", "team/chan/root"),
        AttachmentQueue(budget_bytes=1024),
        "aad-1",
        "tura-1",
        "T1",
        False,
        ujscie,
    )

    assert widziane["ostatni"] is ujscie


# --- Bramki MUTACJI bazy wiedzy (ADR 0065) -------------------------------------


def _para_file_z_mutacja(tmp_path: Path, *, mutations, identities=None, read_authorizer=None):
    return agent_wiring.build_file_support(
        _workspace_settings(tmp_path),
        max_image_edge=2048,
        staged_ext=frozenset({"pdf", "txt"}),
        materialization_limits=_PULAPY,
        mutations=mutations,
        identities=identities,
        read_authorizer=read_authorizer,
    )


class _StubMutations:
    """Sentinel — bramka montażu ma rozstrzygać o OBECNOŚCI akcji, nie o ich działaniu.

    ``allow_delete`` musi tu być, bo od rundy 4 ADR 0068 budowniczy katalogu CZYTA tę bramkę
    z serwisu: kasowanie ma zamykać ``Literal``, a nie dopiero ciało. Domyślnie ``True``, żeby
    ta sonda dalej opisywała najbogatszy wariant, o którym mówi jej nazwa.
    """

    def __init__(self, allow_delete: bool = True) -> None:
        self.allow_delete = allow_delete


class _StubIdentities:
    def __init__(self, person=None) -> None:
        self._person = person

    def resolve_by_aad_user_id(self, aad_user_id: str):  # noqa: ANN201
        return self._person


def _akcje(spec) -> set[str]:  # noqa: ANN001
    import typing

    return set(typing.get_args(typing.get_type_hints(spec.fn)["action"]))


def test_without_the_mutation_gate_the_tool_is_read_only(tmp_path: Path):
    """Warunki decydujące, czy baza wiedzy jest w ogóle mutowalna, muszą mieć sondę —
    inaczej ich usunięcie przechodzi zielono, a zauważa się to na produkcji."""
    from workmate.core.domain.workspace import WorkspaceScope
    from workmate.core.ports.llm import AttachmentQueue

    factory, _stage = _para_file_z_mutacja(tmp_path, mutations=None)
    (spec,) = factory(
        WorkspaceScope("teams_graph", "t/c/r"), AttachmentQueue(budget_bytes=10), "aad-1", "t1"
    )

    assert _akcje(spec) == {"read"}


def test_mutation_gate_without_an_identity_map_stays_read_only(tmp_path: Path):
    """Bez mapy nie ma komu przypisać zmiany ani kogo zapytać o potwierdzenie — fail-closed."""
    from workmate.core.domain.workspace import WorkspaceScope
    from workmate.core.ports.llm import AttachmentQueue

    factory, _stage = _para_file_z_mutacja(tmp_path, mutations=_StubMutations(), identities=None)
    (spec,) = factory(
        WorkspaceScope("teams_graph", "t/c/r"), AttachmentQueue(budget_bytes=10), "aad-1", "t1"
    )

    assert _akcje(spec) == {"read"}


def test_unresolvable_sender_stays_read_only(tmp_path: Path):
    from workmate.core.domain.workspace import WorkspaceScope
    from workmate.core.ports.llm import AttachmentQueue

    factory, _stage = _para_file_z_mutacja(
        tmp_path, mutations=_StubMutations(), identities=_StubIdentities(person=None)
    )
    (spec,) = factory(
        WorkspaceScope("teams_graph", "t/c/r"),
        AttachmentQueue(budget_bytes=10),
        "aad-obcy",
        "t1",
    )

    assert _akcje(spec) == {"read"}


def test_recognised_member_gets_the_mutating_actions(tmp_path: Path):
    from workmate.core.domain.identity import Person
    from workmate.core.domain.workspace import WorkspaceScope
    from workmate.core.ports.llm import AttachmentQueue

    osoba = Person(source_id="anna", display_name="Anna", aad_user_id="aad-1", jira_user="anna.k")
    factory, _stage = _para_file_z_mutacja(
        tmp_path, mutations=_StubMutations(), identities=_StubIdentities(osoba)
    )
    (spec,) = factory(
        WorkspaceScope("teams_graph", "t/c/r"), AttachmentQueue(budget_bytes=10), "aad-1", "t1"
    )

    assert _akcje(spec) == {"read", "edit", "delete"}


def test_member_without_read_authorization_cannot_mutate(tmp_path: Path):
    """ADR 0065 §7: kto nie może CZYTAĆ bazy wiedzy, nie może jej też zmieniać.

    Inaczej bramka odczytu (ADR 0062) przestawałaby cokolwiek znaczyć dla ścieżki NISZCZĄCEJ,
    a odmowa sędziego — niosąca fragment treści — byłaby kanałem odczytu wokół niej.
    """
    from workmate.core.domain.identity import Person
    from workmate.core.domain.workspace import WorkspaceScope
    from workmate.core.errors import NoteAuthorizationError
    from workmate.core.ports.llm import AttachmentQueue

    class _OdmawiajacyAutoryzator:
        def authorize(self, requester_aad_id: str):  # noqa: ANN201
            raise NoteAuthorizationError("nie jest członkiem pionu")

    osoba = Person(source_id="anna", display_name="Anna", aad_user_id="aad-1", jira_user="anna.k")
    factory, _stage = _para_file_z_mutacja(
        tmp_path,
        mutations=_StubMutations(),
        identities=_StubIdentities(osoba),
        read_authorizer=_OdmawiajacyAutoryzator(),
    )
    (spec,) = factory(
        WorkspaceScope("teams_graph", "t/c/r"), AttachmentQueue(budget_bytes=10), "aad-1", "t1"
    )

    assert _akcje(spec) == {"read"}


# --- serwisy odczytu: jeden komplet na proces -----------------------------------


def test_read_services_are_built_once_per_configuration(tmp_path: Path):
    """Regresja wydajności: składanie JEDNYCH drzwi wołało ``_read_services`` trzy razy.

    Każde wywołanie budowało własny ranker semantyczny — własny model ONNX w pamięci i własny
    warmup. Trzy modele zamiast jednego to czysty koszt startu i pamięci; poprawność była cała
    (repozytoria mają własny cache, ranker własne zamki), więc objawem był tylko wolniejszy
    i grubszy proces.
    """
    settings = _settings(tmp_path)

    pierwszy = agent_wiring._read_services(settings)
    drugi = agent_wiring._read_services(settings)

    assert pierwszy[0] is drugi[0]  # NotesService (z rankerem) — ta sama instancja
    assert pierwszy[1] is drugi[1]  # ProjectsService — również


def test_read_services_do_not_leak_between_different_configurations(tmp_path: Path):
    """Memoizacja nie może przeciekać między drzwiami o RÓŻNEJ konfiguracji."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    jedne = agent_wiring._read_services(_settings(tmp_path / "a"))
    drugie = agent_wiring._read_services(_settings(tmp_path / "b"))

    assert jedne[0] is not drugie[0]


def test_read_services_key_includes_the_retrieval_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Ranker czyta ``RetrievalSettings`` z ENV, więc ENV MUSI wchodzić do klucza cache'u.

    Bez tego drzwi zbudowane po zmianie zmiennej dostawałyby ranker z poprzedniego świata —
    po cichu, bo wynik wygląda tak samo, tylko liczy według starej konfiguracji.
    """
    settings = _settings(tmp_path)
    monkeypatch.setenv("WORKMATE_RETRIEVAL_RRF_K", "60")
    przed = agent_wiring._read_services(settings)
    monkeypatch.setenv("WORKMATE_RETRIEVAL_RRF_K", "17")
    po = agent_wiring._read_services(settings)

    assert przed[0] is not po[0]


def test_member_without_jira_account_still_gets_the_mutating_actions(tmp_path: Path):
    """Mutacja notatek jedzie po ``source_id`` osoby, nie po jej koncie Jira (ADR 0070 §3).

    Bliźniak ``test_recognised_member_gets_the_mutating_actions`` dla wpisu „tylko Teams".
    Ta ścieżka jest jedną z dwóch, które ADR nazywa NIEODWRACALNYMI w skutkach (druga to
    powłoka), więc jej zależność od pustego pola nie może zostać bez bramki — a że mutacja
    jest już włączona na flocie, pomyłka byłaby widoczna dopiero na produkcji.
    """
    from workmate.core.domain.identity import Person
    from workmate.core.domain.workspace import WorkspaceScope
    from workmate.core.ports.llm import AttachmentQueue

    osoba = Person(source_id="EMP-51", display_name="Tadeusz", aad_user_id="aad-tadek")
    factory, _stage = _para_file_z_mutacja(
        tmp_path, mutations=_StubMutations(), identities=_StubIdentities(osoba)
    )
    (spec,) = factory(
        WorkspaceScope("teams_graph", "t/c/r"), AttachmentQueue(budget_bytes=10), "aad-tadek", "t1"
    )

    assert _akcje(spec) == {"read", "edit", "delete"}
