"""Wspólne okablowanie runtime'u agenta dla drzwi inbound (CLI, Teams, …).

Buduje ``AgentRuntime`` z tych samych serwisów i jednoźródłowego katalogu co drzwi
MCP. Profil zaufania per drzwi (ADR 0006) jest jawną flagą ``enable_write``:
zaufane drzwi (lokalne CLI) budują katalog READ+WRITE, mniej zaufane (Teams) —
READ-ONLY (agent czyta, nie zapisuje). Import Claude API jest leniwy (w adapterze
outbound); brak extra ``agent`` daje ``ImportError``, który entry-point drzwi
zamienia na czytelny komunikat.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from workmate.adapters.inbound.commands import CommandRouter
from workmate.adapters.inbound.responder import (
    ConversationalResponder,
    Responder,
    SafeResponder,
)
from workmate.adapters.inbound.retrieval_wiring import build_lemmatizer, build_semantic_ranker
from workmate.adapters.outbound.filesystem_outbox import (
    OUTBOX_DIRNAME,
    FilesystemOutboxRepository,
)
from workmate.adapters.outbound.filesystem_skills import read_skill_catalog
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
from workmate.core.agent.prompt import STATIC_PROMPT, static_prompt_for
from workmate.core.agent.runtime import AgentRuntime
from workmate.core.application.compaction import CompactionService
from workmate.core.application.conversations import ConversationService
from workmate.core.application.events import EventService
from workmate.core.application.metrics import MetricsService
from workmate.core.application.outbox import OutboxDelivery, OutboxLimits
from workmate.core.application.services import (
    NotesService,
    NotesWriteService,
    ProjectsService,
)
from workmate.core.application.tools import (
    build_notes_catalog,
    build_notes_read_catalog,
    build_shell_catalog,
    build_tool_catalog,
    build_workspace_catalog,
)
from workmate.core.application.workspace import (
    WorkspaceLimits,
    WorkspaceService,
    WorkspaceWriteService,
)
from workmate.core.ports.command import CommandResult
from workmate.core.ports.outbox import Deliverable

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from workmate.adapters.inbound.brief_command import BriefRouter
    from workmate.adapters.inbound.change_command import ChangeDigestRouter
    from workmate.adapters.inbound.meeting_command import MeetingNoteRouter
    from workmate.adapters.inbound.thread_note_command import ThreadNoteRouter
    from workmate.config import (
        AgentSettings,
        ConversationSettings,
        Settings,
        ShellSettings,
        SkillsSettings,
        WorkspaceSettings,
    )
    from workmate.core.application.tools import ToolSpec
    from workmate.core.domain.workspace import WorkspaceScope
    from workmate.core.ports.command import CommandRunner
    from workmate.core.ports.conversations import ConversationStore
    from workmate.core.ports.repositories import NotesRepository

logger = logging.getLogger(__name__)

# Jedno źródło komunikatu o brakującym extra ``agent`` (dawniej powielone w 4 ``app.py``).
_MISSING_AGENT = "Runtime agenta wymaga extra 'agent'. Zainstaluj: uv sync --extra agent"


def build_notes_service(
    settings: Settings, *, notes_repo: NotesRepository | None = None
) -> NotesService:
    """Zbuduj serwis wyszukiwania notatek z pełnym rankerem (BM25 nad lematami + opcjonalny dense).

    JEDNO źródło budowy rankera dla wszystkich konsumentów: narzędzi agenta, komendy ``/szukaj``
    i CLI ``workmate-search``. Gdyby CLI składało własny wariant, eval retrievalu mierzyłby coś
    innego, niż wykonuje produkcja, a rozjazd byłby niewidoczny do pierwszego złego wyniku.

    ``notes_repo`` podaje wołający, gdy ma już repozytorium do WSPÓŁDZIELENIA (``_read_services``
    daje to samo ``ProjectsService``) — repozytorium cache'uje wczytane notatki, więc druga
    instancja czytałaby ten sam katalog po raz drugi.

    Lematyzator PL (ADR 0023) degraduje łagodnie do rankingu podłańcuchowego przy braku extra
    ``retrieval``. Dense (ADR 0039) powstaje TYLKO obok lematyzatora — fuzja RRF żyje w gałęzi
    BM25, więc sam byłby cichym no-opem.
    """
    retrieval = RetrievalSettings.from_env()
    lemmatizer = build_lemmatizer(retrieval)
    semantic = build_semantic_ranker(retrieval) if lemmatizer is not None else None
    return NotesService(
        notes_repo if notes_repo is not None else MarkdownNotesRepository(settings.notes_dir),
        lemmatizer=lemmatizer,
        semantic=semantic,
        rrf_k=retrieval.rrf_k,
        dense_top_n=retrieval.dense_top_n,
    )


def _read_services(settings: Settings) -> tuple[NotesService, ProjectsService]:
    """Zbuduj serwisy ODCZYTU nad repozytoriami (repo z cache — jeden komplet per wywołanie).

    Drzwi agenta są długożyjące, więc model osadzeń rankera dense ładuje się tu raz.
    """
    notes_repo = MarkdownNotesRepository(settings.notes_dir)
    projects_repo = YamlProjectsRepository(settings.projects_registry)
    return (
        build_notes_service(settings, notes_repo=notes_repo),
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
    system_prompt: str = STATIC_PROMPT,
    shell_available: bool = False,
) -> AgentRuntime:
    """Zbuduj runtime: repozytoria → serwisy → katalog → klient LLM.

    ``enable_write`` steruje profilem zaufania drzwi: ``True`` → ``Notes`` z akcją ``save``
    (zaufane, np. lokalne CLI); ``False`` → wariant tylko do odczytu (mniej zaufane drzwi,
    np. Teams — ADR 0006). ``extra_catalog`` (ADR 0019/0020) to STATYCZNE narzędzia per drzwi
    (np. odczyt zdarzeń, narzędzia GitHub) doklejane do bazowego katalogu — z definicji poza
    powierzchnią MCP (golden-test nietknięty).
    ``system_prompt`` pozwala drzwiom doprecyzować zdolności (np. multimodal tylko tam, gdzie
    materializujemy załączniki); domyślnie bazowy ``STATIC_PROMPT`` (ADR 0056).

    ``shell_available`` mówi, czy te drzwi dają agentowi ``Bash`` (ADR 0057). Steruje trzema
    narzędziami ODCZYTU bazy wiedzy: z powłoką są zbędne (``workmate-search`` plus ``cat``
    na montażu ``ro``), bez niej są JEDYNĄ drogą do notatek. Domyślne ``False`` jest celowo
    zachowawcze — drzwi, które zapomną o tym parametrze, dostają katalog pełniejszy, a nie
    agenta odciętego od bazy wiedzy.
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
    # Powierzchnia agenta jest OSOBNA od powierzchni MCP (ADR 0009, krok 5.4): skonsolidowane
    # ``Notes`` zamiast ``get_project_status`` i ``save_note``, a trzy narzędzia odczytu bazy
    # wiedzy warunkowo — zastępuje je powłoka, której przy wyłączonej bramce po prostu nie ma.
    catalog = [
        *build_notes_catalog(
            projects_service, write_service=write_service, shell_available=shell_available
        ),
        *([] if shell_available else build_notes_read_catalog(notes_service, projects_service)),
    ]
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
    system_prompt: str = STATIC_PROMPT,
    shell_available: bool = False,
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
            shell_available=shell_available,
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


class _ScopedRunner:
    """``CommandRunner`` zapewniający istnienie katalogu rozmowy przed wysłaniem polecenia.

    Wykonawca, gdy podany ``cwd`` nie istnieje, degraduje do swojego katalogu domyślnego —
    rozsądnie, bo ``Popen`` z nieistniejącym ``cwd`` rzuca błędem mówiącym o katalogu zamiast
    o poleceniu. Skutkiem ubocznym byłaby jednak UTRATA IZOLACJI: katalog rozmowy powstaje
    leniwie, przy pierwszym ``create_file``, więc do tego czasu wszystkie rozmowy dzieliłyby
    wspólny korzeń brudnopisu i widziały nawzajem swoje pliki. Zmierzone: ``pwd`` w świeżej
    rozmowie zwracało ``/home/scratchpad``, nie ``/home/scratchpad/<kanał>/<hash>``.

    Katalog zakłada APLIKACJA, nie wykonawca: „rozmowa" to pojęcie aplikacji, a wykonawca ma
    zostać procesem bez wiedzy o tym, co znaczą ścieżki, które dostaje. Nieudany zapis wraca
    jako wynik z niezerowym kodem — jak każda inna porażka polecenia (ADR 0057).
    """

    def __init__(self, inner: CommandRunner, *, with_outbox: bool = False) -> None:
        self._inner = inner
        # Skrzynkę zakładamy TYLKO, gdy jest kto ją opróżnia. Katalog tworzony przy wyłączonej
        # dostawie byłby zaproszeniem do zapisu, którego nikt nie odbiera — i rósłby bez końca.
        self._with_outbox = with_outbox

    def run(self, command: str, *, cwd: str = "", timeout_s: float = 0) -> CommandResult:
        if cwd:
            try:
                # Skrzynkę nadawczą zakłada aplikacja razem z katalogiem roboczym, bo opis
                # narzędzia każe modelowi pisać do ``outputs/`` ścieżką WZGLĘDNĄ — a
                # przekierowanie powłoki do nieistniejącego katalogu kończy się błędem,
                # nie utworzeniem go.
                target = Path(cwd, OUTBOX_DIRNAME) if self._with_outbox else Path(cwd)
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return CommandResult(
                    exit_code=-1,
                    stdout="",
                    stderr=f"Nie udało się przygotować katalogu roboczego {cwd}: {exc}",
                )
        return self._inner.run(command, cwd=cwd, timeout_s=timeout_s)


def _build_shell_factory(
    shell_settings: ShellSettings,
    workspace_settings: WorkspaceSettings,
    *,
    outbox_enabled: bool = False,
) -> Callable[[WorkspaceScope], list[ToolSpec]] | None:
    """Fabryka narzędzia ``Bash`` (ADR 0057) wiążącego polecenia z katalogiem rozmowy.

    Zwraca ``None``, gdy powłoka jest wyłączona ALBO gdy klienta wykonawcy nie da się
    zaimportować — jest POSIX-only (gniazda unix), więc na maszynie deweloperskiej z Windows
    degradujemy do „brak narzędzia" zamiast wywracać start drzwi. Import jest leniwy z tego
    samego powodu co Claude API.

    ``workspace_settings.workspace_dir`` jest korzeniem ścieżek dla OBU stron: aplikacja pisze
    tam pliki narzędziem ``create_file``, a wykonawca dostaje ten sam katalog jako ``cwd``.
    Rozjazd tych dwóch wartości oznaczałby, że model tworzy plik narzędziem i nie widzi go
    powłoką — dlatego korzeń bierzemy z jednej konfiguracji, a nie z dwóch.
    """
    if not shell_settings.enabled:
        return None
    try:
        from workmate.adapters.outbound.exec_client import SocketCommandRunner
    except ImportError:
        logger.info(
            "Klient wykonawcy jest POSIX-only — narzędzie powłoki pomijam na tej platformie."
        )
        return None

    runner = _ScopedRunner(
        SocketCommandRunner(shell_settings.socket_path), with_outbox=outbox_enabled
    )
    workspace_root = workspace_settings.workspace_dir.as_posix()

    def factory(scope: WorkspaceScope) -> list[ToolSpec]:
        return build_shell_catalog(
            scope,
            runner,
            workspace_root=workspace_root,
            default_timeout_s=shell_settings.default_timeout_s,
            outbox_enabled=outbox_enabled,
        )

    return factory


def _build_outbox_delivery(
    workspace_settings: WorkspaceSettings,
    send_factory: Callable[[str], Callable[[Deliverable], None] | None],
    *,
    max_file_bytes: int,
    max_files_per_turn: int,
    max_seconds: float,
) -> _ScopedOutbox:
    """Zbuduj dostawę ze skrzynki nadawczej rozmowy — wołaną PO turze, zwracającą zdanie raportu.

    Korzeń bierzemy z ``workspace_settings``, tego samego, z którego liczy się ``cwd`` poleceń
    (``_build_shell_factory``) — rozjazd tych dwóch wartości oznaczałby, że model zapisuje plik
    w skrzynce, której drzwi nie czytają, i to bez żadnego objawu poza brakiem załącznika.
    """
    delivery = OutboxDelivery(
        FilesystemOutboxRepository(workspace_settings.workspace_dir),
        OutboxLimits(
            max_file_bytes=max_file_bytes,
            max_files_per_turn=max_files_per_turn,
            max_total_seconds=max_seconds,
        ),
    )

    return _ScopedOutbox(delivery, send_factory)


class _ScopedOutbox:
    """Dwufazowa dostawa dla respondera: migawka na starcie tury, wysyłka po niej.

    Fazy są DWIE, bo migawka jest granicą pochodzenia plików — musi powstać, zanim model
    dostanie powłokę. Zwinięcie ich w jedno wywołanie po turze znaczyłoby, że nie umiemy
    odróżnić pliku wytworzonego w tej turze od podłożonego wcześniej z innej rozmowy.
    """

    def __init__(
        self,
        delivery: OutboxDelivery,
        send_factory: Callable[[str], Callable[[Deliverable], None] | None],
    ) -> None:
        self._delivery = delivery
        self._send_factory = send_factory

    def snapshot(self, scope: WorkspaceScope) -> None:
        self._delivery.snapshot(str(scope.dirpath()))

    def deliver(self, scope: WorkspaceScope) -> str:
        send = self._send_factory(scope.conversation)
        if send is None:
            # Wątek bez celu dostawy (np. rozmowa spoza kanału): zostawiamy skrzynkę nietkniętą,
            # bo plik nie jest odrzucony — po prostu nie ma dokąd pójść z TYCH drzwi.
            return ""
        return self._delivery.deliver(str(scope.dirpath()), send).notice()


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
    shell_settings: ShellSettings | None = None,
    extra_catalog: Sequence[ToolSpec] = (),
    thread_tool_factory: Callable[[str], Sequence[ToolSpec]] | None = None,
    user_push_tool_factory: Callable[[str], Sequence[ToolSpec]] | None = None,
    my_jira_tasks_factory: Callable[[str], Sequence[ToolSpec]] | None = None,
    meeting_notes: MeetingNoteRouter | None = None,
    thread_note: ThreadNoteRouter | None = None,
    project_brief: BriefRouter | None = None,
    change_digest: ChangeDigestRouter | None = None,
    supports_attachments: bool = False,
    outbox_send_factory: Callable[[str], Callable[[Deliverable], None] | None] | None = None,
    outbox_max_file_bytes: int = 0,
    outbox_max_files_per_turn: int = 5,
    outbox_max_seconds: float = 20.0,
    skills_settings: SkillsSettings | None = None,
) -> Responder:
    """Złóż całą receptę drzwi: runtime → store → pamięć → kompaktowanie → router komend.

    Jedno źródło recepty ``SafeResponder(ConversationalResponder(...))`` (dawniej skopiowanej
    w 4 drzwiach). ``safe=True`` owija w ``SafeResponder`` (drzwi async); ``show_thinking`` tylko
    dla drzwi zaufanych (CLI). Router komend dostaje katalog READ-ONLY (bramka ADR 0006).
    ``enable_workspace`` (osobna bramka, ADR 0018) dokłada agentowi narzędzia katalogu roboczego,
    a ``shell_settings.enabled`` (znów osobna, ADR 0057) — narzędzie ``Bash`` biegnące
    w kontenerze-wykonawcy bez sieci.
    ``extra_catalog`` (ADR 0019/0020) to statyczne narzędzia per drzwi (odczyt zdarzeń, GitHub) —
    poza powierzchnią MCP; router komend ich NIE dostaje (pozostaje read-only nad notatkami).
    ``thread_tool_factory``/``user_push_tool_factory``/``my_jira_tasks_factory``
    (ADR 0024/0027/0054) wstrzykują narzędzia PER TURĘ wiązane, odpowiednio, z wątkiem
    (external_id) i z nadawcą (sender_id) — poza powierzchnią MCP. ``my_jira_tasks_factory``
    zasila też komendę ``/moje-zadania`` w routerze (jedno miejsce rozwiązywania tożsamości).
    ``supports_attachments`` (F8) uwidacznia zdolność multimodalną (prompt + ``/pomoc``) tylko na
    drzwiach z materializerem załączników — inaczej byłaby mylną obietnicą na drzwiach tekstowych.
    ``outbox_send_factory`` (ADR 0009 paczki) wiąże skrzynkę nadawczą rozmowy z drogą dostawy per
    drzwi: z ``external_id`` daje wysyłacz albo ``None`` (wątek bez celu dostawy). Wymaga
    ``workspace_settings`` — skrzynka leży w katalogu roboczym rozmowy, więc bez wspólnego korzenia
    drzwi szukałyby plików gdzie indziej, niż zapisuje je wykonawca.
    """
    # Skrzynka nadawcza ma własną bramkę po stronie drzwi (``enable_file_reply``), niezależną od
    # powłoki. Rozstrzygamy ją PRZED zbudowaniem powłoki, bo opis narzędzia ``Bash`` obiecuje
    # dostawę przez ``outputs/`` — a obietnica przy wyłączonej dostawie byłaby tym samym
    # defektem, który ta zdolność likwiduje: zapis kończy się kodem 0 i ciszą.
    outbox_enabled = (
        outbox_send_factory is not None
        and workspace_settings is not None
        and outbox_max_file_bytes > 0
    )
    # Powłoka (ADR 0057) ma WŁASNĄ bramkę i własny profil zaufania, ale dzieli korzeń ścieżek
    # z katalogiem roboczym — dlatego wymaga ``workspace_settings`` nawet przy wyłączonych
    # plikach: bez wspólnego korzenia ``cwd`` poleceń rozjechałby się z miejscem, w którym
    # narzędzia plikowe zapisują.
    shell_factory = (
        _build_shell_factory(shell_settings, workspace_settings, outbox_enabled=outbox_enabled)
        if shell_settings is not None and workspace_settings is not None
        else None
    )
    runtime = build_agent_runtime_or_exit(
        settings,
        agent_settings,
        enable_write=enable_write,
        extra_catalog=extra_catalog,
        system_prompt=static_prompt_for(attachments=supports_attachments),
        # Z FABRYKI, nie z ustawień. `shell_settings.enabled` mówi, czego chciał operator;
        # `shell_factory` — co agent faktycznie dostanie. Rozjeżdżają się przy braku
        # `workspace_settings` i na platformie, gdzie klient wykonawcy nie importuje się
        # (POSIX-only). Rozjazd oznaczałby agenta bez powłoki I bez narzędzi odczytu, czyli
        # bez jakiejkolwiek drogi do bazy wiedzy — po cichu.
        shell_available=shell_factory is not None,
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
        my_jira_tasks=my_jira_tasks_factory,
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
    # Procedury z `/mnt/skills` (ADR 0005) — odczyt RAZ przy składaniu drzwi. Brak katalogu daje
    # pustą listę i zachowanie dokładnie dawne; nagłówek sesji nie dostaje wtedy sekcji skilli.
    skills = (
        read_skill_catalog(skills_settings.skills_dir, limit=skills_settings.max_in_header)
        if skills_settings is not None
        else ()
    )
    # Dostawa ze skrzynki dzieli korzeń z powłoką i katalogiem roboczym; ``outbox_enabled``
    # rozstrzygnięto wyżej, razem z opisem narzędzia, żeby obietnica i zdolność miały jedno źródło.
    outbox_delivery = (
        _build_outbox_delivery(
            workspace_settings,  # type: ignore[arg-type]  # zawężone przez ``outbox_enabled``
            outbox_send_factory,  # type: ignore[arg-type]
            max_file_bytes=outbox_max_file_bytes,
            max_files_per_turn=outbox_max_files_per_turn,
            max_seconds=outbox_max_seconds,
        )
        if outbox_enabled
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
        shell_catalog_factory=shell_factory,
        thread_tool_factory=thread_tool_factory,
        user_push_tool_factory=user_push_tool_factory,
        my_jira_tasks_factory=my_jira_tasks_factory,
        meeting_notes=meeting_notes,
        thread_note=thread_note,
        project_brief=project_brief,
        change_digest=change_digest,
        metrics=metrics,
        outbox_delivery=outbox_delivery,
        skills=skills,
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

    Czyszczenie wyników narzędzi (ADR 0058) jest tu WYŁĄCZONE: streszczacz dostaje jedną
    wiadomość ze spłaszczonym transkryptem, więc nie ma czego czyścić, a nagłówek bety
    zostawałby na wywołaniu, które z niej nie korzysta.
    """
    if not conversation_settings.compaction_enabled:
        return None
    from workmate.adapters.outbound.anthropic_llm import AnthropicLLMClient

    model = conversation_settings.compaction_model or agent_settings.model
    summarizer = AnthropicLLMClient(
        replace(agent_settings, model=model, context_editing_enabled=False)
    )
    return CompactionService(
        store,
        summarizer,
        threshold_tokens=conversation_settings.compaction_threshold_tokens,
        keep_turns=conversation_settings.compaction_keep_turns,
    )
