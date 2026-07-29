"""Wspólne okablowanie runtime'u agenta dla drzwi inbound (CLI, Telegram, …).

Buduje ``AgentRuntime`` z tych samych serwisów i jednoźródłowego katalogu co drzwi
MCP. Profil zaufania per drzwi (ADR 0006) jest jawną flagą ``enable_write``:
zaufane drzwi (lokalne CLI) budują katalog READ+WRITE, mniej zaufane (Telegram) —
READ-ONLY (agent czyta, nie zapisuje). Import Claude API jest leniwy (w adapterze
outbound); brak extra ``agent`` daje ``ImportError``, który entry-point drzwi
zamienia na czytelny komunikat.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from workmate.adapters.inbound.commands import CommandRouter
from workmate.adapters.inbound.responder import (
    ConversationalResponder,
    Responder,
    SafeResponder,
)
from workmate.adapters.inbound.retrieval_wiring import build_lemmatizer, build_semantic_ranker
from workmate.adapters.outbound.filesystem_workspace import (
    FilesystemWorkspaceRepository,
    FilesystemWorkspaceWriter,
)
from workmate.adapters.outbound.markdown_notes_repo import MarkdownNotesRepository
from workmate.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
from workmate.adapters.outbound.sqlite_conversations import SqliteConversationStore
from workmate.adapters.outbound.sqlite_metrics import SqliteMetricsStore
from workmate.adapters.outbound.yaml_projects_repo import YamlProjectsRepository
from workmate.config import RetrievalSettings
from workmate.core.agent.prompt import SYSTEM_PROMPT, system_prompt_for
from workmate.core.agent.runtime import AgentRuntime
from workmate.core.application.compaction import CompactionService
from workmate.core.application.conversations import ConversationService
from workmate.core.application.events import EventService
from workmate.core.application.metrics import MetricsService
from workmate.core.application.services import (
    NotesService,
    NotesWriteService,
    ProjectsService,
)
from workmate.core.application.tools import build_tool_catalog, build_workspace_catalog
from workmate.core.application.workspace import (
    WorkspaceLimits,
    WorkspaceService,
    WorkspaceWriteService,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from workmate.adapters.inbound.meeting_command import MeetingNoteRouter
    from workmate.config import (
        AgentSettings,
        ConversationSettings,
        Settings,
        WorkspaceSettings,
    )
    from workmate.core.application.tools import ToolSpec
    from workmate.core.domain.workspace import WorkspaceScope
    from workmate.core.ports.conversations import ConversationStore

# Jedno źródło komunikatu o brakującym extra ``agent`` (dawniej powielone w 4 ``app.py``).
_MISSING_AGENT = "Runtime agenta wymaga extra 'agent'. Zainstaluj: uv sync --extra agent"


def _read_services(settings: Settings) -> tuple[NotesService, ProjectsService]:
    """Zbuduj serwisy ODCZYTU nad repozytoriami (repo z cache — jeden komplet per wywołanie).

    ``NotesService`` dostaje lematyzator PL (ADR 0023) z fallbackiem na brak extra — lepszy
    ranking wyszukiwania (BM25 nad lematami) na wszystkich drzwiach agenta.
    """
    notes_repo = MarkdownNotesRepository(settings.notes_dir)
    projects_repo = YamlProjectsRepository(settings.projects_registry)
    retrieval = RetrievalSettings.from_env()
    lemmatizer = build_lemmatizer(retrieval)
    # Dense (ADR 0039, Faza B) żyje w gałęzi BM25 — budujemy go TYLKO obok lematyzatora (bez niego
    # byłby cichym no-opem). Drzwi agenta są długożyjące, więc model osadzeń ładuje się raz.
    semantic = build_semantic_ranker(retrieval) if lemmatizer is not None else None
    return (
        NotesService(
            notes_repo,
            lemmatizer=lemmatizer,
            semantic=semantic,
            rrf_k=retrieval.rrf_k,
            dense_top_n=retrieval.dense_top_n,
        ),
        ProjectsService(projects_repo, notes_repo, events=_events_if_present()),
    )


def _events_if_present() -> EventService | None:
    """``EventService`` nad wspólnym ``events.db`` — TYLKO gdy plik istnieje (most w użyciu).

    Wzbogaca ``get_project_status`` o aktywność GitHub (ADR 0029). Bez pliku ``None`` — drzwi
    agenta bez mostu NIE tworzą pustego ``events.db`` tylko pod odczyt statusu.
    """
    from pathlib import Path

    from workmate.adapters.outbound.sqlite_events import SqliteEventStore
    from workmate.config import EventsSettings

    path = EventsSettings.from_env().db_path
    if not Path(str(path)).expanduser().exists():
        return None
    return EventService(SqliteEventStore(path))


def build_agent_runtime(
    settings: Settings,
    agent_settings: AgentSettings,
    *,
    enable_write: bool,
    extra_catalog: Sequence[ToolSpec] = (),
    system_prompt: str = SYSTEM_PROMPT,
) -> AgentRuntime:
    """Zbuduj runtime: repozytoria → serwisy → katalog → klient LLM.

    ``enable_write`` steruje profilem zaufania drzwi: ``True`` → katalog z
    ``save_note`` (zaufane, np. lokalne CLI); ``False`` → katalog tylko do odczytu
    (mniej zaufane drzwi, np. Telegram — ADR 0006). ``extra_catalog`` (ADR 0019/0020) to
    STATYCZNE narzędzia per drzwi (np. odczyt zdarzeń, narzędzia GitHub) doklejane do
    bazowego katalogu — z definicji poza powierzchnią MCP (golden-test nietknięty).
    ``system_prompt`` pozwala drzwiom doprecyzować zdolności (np. multimodal tylko tam, gdzie
    materializujemy załączniki); domyślnie bazowy ``SYSTEM_PROMPT``.
    """
    from workmate.adapters.outbound.anthropic_llm import AnthropicLLMClient

    notes_service, projects_service = _read_services(settings)
    write_service = (
        NotesWriteService(
            MarkdownNotesWriter(settings.notes_dir),
            YamlProjectsRepository(settings.projects_registry),
        )
        if enable_write
        else None
    )
    catalog = build_tool_catalog(notes_service, projects_service, write_service=write_service)
    return AgentRuntime(
        AnthropicLLMClient(agent_settings),
        [*catalog, *extra_catalog],
        system_prompt=system_prompt,
        max_tool_iterations=agent_settings.max_tool_iterations,
    )


def build_agent_runtime_or_exit(
    settings: Settings,
    agent_settings: AgentSettings,
    *,
    enable_write: bool,
    extra_catalog: Sequence[ToolSpec] = (),
    system_prompt: str = SYSTEM_PROMPT,
) -> AgentRuntime:
    """Jak ``build_agent_runtime``, ale brak extra ``agent`` → czytelny ``SystemExit``.

    Uwspólnia powtarzany w 4 drzwiach blok ``try build_agent_runtime except ImportError``.
    """
    try:
        return build_agent_runtime(
            settings,
            agent_settings,
            enable_write=enable_write,
            extra_catalog=extra_catalog,
            system_prompt=system_prompt,
        )
    except ImportError as exc:
        raise SystemExit(_MISSING_AGENT) from exc


def build_read_catalog(settings: Settings) -> list[ToolSpec]:
    """Katalog narzędzi TYLKO DO ODCZYTU (bez ``save_note``) — dla komend read-only.

    Zawsze read-only, niezależnie od profilu drzwi: strukturalna gwarancja, że komendy
    (``/szukaj`` itd.) nie omijają bramki zapisu (ADR 0006), nawet na CLI z ``enable_write``.
    """
    notes, projects = _read_services(settings)
    return build_tool_catalog(notes, projects, write_service=None)


def _build_workspace_factory(
    workspace_settings: WorkspaceSettings,
) -> Callable[[WorkspaceScope], list[ToolSpec]]:
    """Fabryka narzędzi KATALOGU ROBOCZEGO (ADR 0018) wiążących je ze scope rozmowy.

    Serwisy (read/write) budujemy RAZ; fabryka na turę tylko domyka je scope'em rozmowy —
    izolacja per rozmowa bez współdzielonego stanu w runtime.
    """
    repo = FilesystemWorkspaceRepository(workspace_settings.workspace_dir)
    limits = WorkspaceLimits(
        max_file_bytes=workspace_settings.max_file_mb * 1024 * 1024,
        max_files_per_scope=workspace_settings.max_files_per_scope,
        max_total_bytes=workspace_settings.max_total_mb * 1024 * 1024,
        allowed_ext=frozenset(workspace_settings.allowed_ext),
    )
    read_service = WorkspaceService(repo)
    write_service = WorkspaceWriteService(
        FilesystemWorkspaceWriter(workspace_settings.workspace_dir), repo, limits
    )

    def factory(scope: WorkspaceScope) -> list[ToolSpec]:
        return build_workspace_catalog(scope, read_service, write_service)

    return factory


def build_conversational_responder(
    settings: Settings,
    agent_settings: AgentSettings,
    conversation_settings: ConversationSettings,
    *,
    channel: str,
    enable_write: bool,
    safe: bool,
    show_thinking: bool = False,
    enable_workspace: bool = False,
    workspace_settings: WorkspaceSettings | None = None,
    extra_catalog: Sequence[ToolSpec] = (),
    thread_tool_factory: Callable[[str], Sequence[ToolSpec]] | None = None,
    user_push_tool_factory: Callable[[str], Sequence[ToolSpec]] | None = None,
    meeting_notes: MeetingNoteRouter | None = None,
    supports_attachments: bool = False,
) -> Responder:
    """Złóż całą receptę drzwi: runtime → store → pamięć → kompaktowanie → router komend.

    Jedno źródło recepty ``SafeResponder(ConversationalResponder(...))`` (dawniej skopiowanej
    w 4 drzwiach). ``safe=True`` owija w ``SafeResponder`` (drzwi async); ``show_thinking`` tylko
    dla drzwi zaufanych (CLI). Router komend dostaje katalog READ-ONLY (bramka ADR 0006).
    ``enable_workspace`` (osobna bramka, ADR 0018) dokłada agentowi narzędzia katalogu roboczego.
    ``extra_catalog`` (ADR 0019/0020) to statyczne narzędzia per drzwi (odczyt zdarzeń, GitHub) —
    poza powierzchnią MCP; router komend ich NIE dostaje (pozostaje read-only nad notatkami).
    ``thread_tool_factory``/``user_push_tool_factory`` (ADR 0024/0027) wstrzykują narzędzia PER TURĘ
    wiązane, odpowiednio, z wątkiem (external_id) i z nadawcą (sender_id) — poza powierzchnią MCP.
    ``supports_attachments`` (F8) uwidacznia zdolność multimodalną (prompt + ``/pomoc``) tylko na
    drzwiach z materializerem załączników — inaczej byłaby mylną obietnicą na drzwiach tekstowych.
    """
    runtime = build_agent_runtime_or_exit(
        settings,
        agent_settings,
        enable_write=enable_write,
        extra_catalog=extra_catalog,
        system_prompt=system_prompt_for(attachments=supports_attachments),
    )
    store = SqliteConversationStore(conversation_settings.db_path)
    conversations = ConversationService(
        store,
        max_context_tokens=conversation_settings.max_context_tokens,
        idle_timeout=conversation_settings.idle_timeout(),
        size_rollover=not conversation_settings.compaction_enabled,
    )
    compaction = build_compaction_service(agent_settings, conversation_settings, store)
    router = CommandRouter(
        conversations,
        {spec.name: spec.fn for spec in build_read_catalog(settings)},
        supports_attachments=supports_attachments,
    )
    workspace_factory = (
        _build_workspace_factory(workspace_settings)
        if enable_workspace and workspace_settings is not None
        else None
    )
    # Licznik wywołań (Tor A): włączony obecnością WORKMATE_METRICS_DB; ``None`` → wyłączony,
    # responder nie zapisuje nic. Jeden punkt wpięcia obejmuje wszystkie drzwi agentowe.
    metrics = (
        MetricsService(SqliteMetricsStore(settings.metrics_db))
        if settings.metrics_db is not None
        else None
    )
    inner = ConversationalResponder(
        runtime,
        conversations,
        channel=channel,
        show_thinking=show_thinking,
        compaction=compaction,
        commands=router,
        workspace_catalog_factory=workspace_factory,
        thread_tool_factory=thread_tool_factory,
        user_push_tool_factory=user_push_tool_factory,
        meeting_notes=meeting_notes,
        metrics=metrics,
    )
    return SafeResponder(inner) if safe else inner


def build_compaction_service(
    agent_settings: AgentSettings,
    conversation_settings: ConversationSettings,
    store: ConversationStore,
) -> CompactionService | None:
    """Zbuduj serwis kompaktowania (ADR 0014) albo ``None``, gdy wyłączone w konfiguracji.

    Model podsumowań to ``compaction_model`` (pusty → model agenta). Reużywamy adapter
    ``AnthropicLLMClient`` jako klienta podsumowującego — bez nowego portu — z tym samym
    kluczem/ustawieniami co agent, tylko z podmienionym modelem. Klient dzieli MAGAZYN z
    ``ConversationService`` (ten sam plik SQLite), więc archiwizacja i podsumowania idą do
    tej samej bazy. Import Claude API jest tu już bezpieczny — runtime zbudowano wcześniej.
    """
    if not conversation_settings.compaction_enabled:
        return None
    from workmate.adapters.outbound.anthropic_llm import AnthropicLLMClient

    model = conversation_settings.compaction_model or agent_settings.model
    summarizer = AnthropicLLMClient(replace(agent_settings, model=model))
    return CompactionService(
        store,
        summarizer,
        threshold_tokens=conversation_settings.compaction_threshold_tokens(),
        keep_turns=conversation_settings.compaction_keep_turns,
    )
