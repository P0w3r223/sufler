"""Wspólne okablowanie runtime'u agenta dla drzwi inbound (CLI, Teams, …).

Buduje ``AgentRuntime`` z tych samych serwisów i jednoźródłowego katalogu co drzwi
MCP. Profil zaufania per drzwi (ADR 0006) jest jawną flagą ``enable_write``:
zaufane drzwi (lokalne CLI) budują katalog READ+WRITE, mniej zaufane (Teams) —
READ-ONLY (agent czyta, nie zapisuje). Import Claude API jest leniwy (w adapterze
outbound); brak extra ``agent`` daje ``ImportError``, który entry-point drzwi
zamienia na czytelny komunikat.

Pakiet, nie moduł: fabryki poszczególnych ZDOLNOŚCI mieszkają w modułach obok
(``notes_read``, ``workspace``, ``file_support``, ``shell``, ``outbox``), a tutaj zostają
PUNKTY SKŁADANIA — ``build_agent_runtime``, ``build_conversational_responder``,
``build_compaction_service``. Ten plik jest JEDYNYM wejściem: wszystko, co repo importuje
z ``sufler.adapters.inbound.agent_wiring``, działa jak przed rozbiciem, łącznie
z podmianą atrybutów przez ``monkeypatch.setattr(agent_wiring, …)`` w testach — nazwy są
globalami TEGO modułu, więc podmiana trafia dokładnie tam, gdzie szuka ich wołający.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from sufler.adapters.inbound.commands import CommandRouter
from sufler.adapters.inbound.responder import (
    ConversationalResponder,
    Responder,
    SafeResponder,
)
from sufler.adapters.outbound.anthropic_judge import AnthropicMutationJudge
from sufler.adapters.outbound.filesystem_skills import read_skill_catalog
from sufler.adapters.outbound.filesystem_snapshots import FilesystemNoteSnapshots
from sufler.adapters.outbound.markdown_notes_repo import MarkdownNotesRepository
from sufler.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
from sufler.adapters.outbound.memory_confirmations import InMemoryConfirmations
from sufler.adapters.outbound.sqlite_audit import SqliteAuditStore
from sufler.adapters.outbound.sqlite_conversations import SqliteConversationStore
from sufler.adapters.outbound.sqlite_metrics import SqliteMetricsStore
from sufler.adapters.outbound.yaml_projects_repo import YamlProjectsRepository
from sufler.core.agent.prompt import static_prompt_for
from sufler.core.agent.runtime import AgentRuntime
from sufler.core.application.audit import AuditService
from sufler.core.application.compaction import CompactionService
from sufler.core.application.conversations import ConversationService
from sufler.core.application.metrics import MetricsService
from sufler.core.application.note_mutation import NoteMutationService
from sufler.core.application.services import (
    NotesWriteService,
)
from sufler.core.application.tools import (
    build_agent_notes_read_catalog,
    build_project_catalog,
)
from sufler.core.ports.materialization import MaterializationLimits
from sufler.core.ports.outbox import Deliverable

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sufler.adapters.inbound.brief_command import BriefRouter
    from sufler.adapters.inbound.change_command import ChangeDigestRouter
    from sufler.adapters.inbound.meeting_command import MeetingNoteRouter
    from sufler.adapters.inbound.thread_note_command import ThreadNoteRouter
    from sufler.config import (
        AgentSettings,
        ConversationSettings,
        Settings,
        ShellSettings,
        SkillsSettings,
        WorkspaceSettings,
    )
    from sufler.core.application.note_mutation import NoteMutationService
    from sufler.core.application.note_read_authz import NoteReadAuthorizer
    from sufler.core.application.shell_authz import ShellAuthorizer
    from sufler.core.application.tools import ToolSpec
    from sufler.core.ports.conversations import ConversationStore
    from sufler.core.ports.identity import AadIdentityLookup


from sufler.adapters.inbound.agent_wiring.file_support import build_file_support
from sufler.adapters.inbound.agent_wiring.notes_read import (
    _build_notes_read_factory,
    _read_services,
    build_notes_service,
    build_read_catalog,
)
from sufler.adapters.inbound.agent_wiring.outbox import _build_outbox_delivery
from sufler.adapters.inbound.agent_wiring.shell import _build_shell_factory, _ScopedRunner
from sufler.adapters.inbound.agent_wiring.workspace import _build_workspace_factory

# Re-eksport z nazwami prywatnymi to nie przeoczenie: repo importuje stąd ``_ScopedRunner``,
# ``_build_notes_read_factory`` i ``_build_outbox_delivery``, a testy podmieniają
# ``_build_shell_factory``/``_read_services``/``build_file_support`` jako ATRYBUTY tego modułu.
# Bez ``__all__`` ruff zdjąłby importy, których samo składanie nie woła.
__all__ = [
    "_ScopedRunner",
    "_build_notes_read_factory",
    "_build_outbox_delivery",
    "_build_shell_factory",
    "_build_workspace_factory",
    "_read_services",
    "build_agent_runtime",
    "build_agent_runtime_or_exit",
    "build_compaction_service",
    "build_conversational_responder",
    "build_file_support",
    "build_notes_service",
    "build_read_catalog",
]

logger = logging.getLogger(__name__)

# Jedno źródło komunikatu o brakującym extra ``agent`` (dawniej powielone w 4 ``app.py``).
_MISSING_AGENT = "Runtime agenta wymaga extra 'agent'. Zainstaluj: uv sync --extra agent"

# Domyślne pułapy ``File`` dla drzwi, które go nie budują: same zera, czyli KAŻDY plik odpada.
# Fail-closed rozmyślnie — drzwi, które chcą narzędzia, muszą podać własne liczby, a nie
# odziedziczyć hojny domyślny sufit z sygnatury.
_BEZ_PULAPOW = MaterializationLimits(max_bytes=0, max_extract_bytes=0)


def build_agent_runtime(
    settings: Settings,
    agent_settings: AgentSettings,
    *,
    enable_write: bool,
    extra_catalog: Sequence[ToolSpec] = (),
    system_prompt: str | None = None,
    shell_available: bool = False,
    suppress_notes_read: bool = False,
) -> AgentRuntime:
    """Zbuduj runtime: repozytoria → serwisy → katalog → klient LLM.

    ``enable_write`` steruje profilem zaufania drzwi: ``True`` → ``Project`` z akcją ``save``
    (zaufane, np. lokalne CLI); ``False`` → wariant tylko do odczytu (mniej zaufane drzwi,
    np. Teams — ADR 0006). ``extra_catalog`` (ADR 0019/0020) to STATYCZNE narzędzia per drzwi
    (np. odczyt zdarzeń, narzędzia GitHub) doklejane do bazowego katalogu — z definicji poza
    powierzchnią MCP (golden-test nietknięty).
    ``system_prompt`` pozwala drzwiom doprecyzować zdolności (np. multimodal tylko tam, gdzie
    materializujemy załączniki). ``None`` wyprowadza korpus z ``shell_available`` (ADR 0056,
    etap 6 planu przebudowy) — a nie ze stałej. Stała jako domyślna wiązała drzwi z powłoką
    i opisem świata BEZ powłoki: agent czytał „the knowledge base lives behind tools", dostając
    katalog, z którego te narzędzia właśnie usunięto. Rozjazd był po cichy i możliwy wyłącznie
    przez przeoczenie jednego argumentu.

    ``shell_available`` mówi, czy te drzwi dają agentowi ``Bash`` (ADR 0057). Steruje trzema
    narzędziami ODCZYTU bazy wiedzy oraz wariantem sekcji ``ENVIRONMENT``: z powłoką narzędzia
    są zbędne (``sufler-search`` plus ``cat`` na montażu ``ro``) i świat opisują montaże, bez
    niej narzędzia są JEDYNĄ drogą do notatek i to one są światem. Domyślne ``False`` jest celowo
    zachowawcze — drzwi, które zapomną o tym parametrze, dostają katalog pełniejszy, a nie
    agenta odciętego od bazy wiedzy.

    ``suppress_notes_read`` zdejmuje z katalogu BAZOWEGO CAŁĄ powierzchnię bazy wiedzy — trzy
    narzędzia odczytu ORAZ ``Project`` — także wtedy, gdy powłoki nie ma, bo przejmuje ją
    PER-TUROWA fabryka bramkowana nadawcą (autoryzacja odczytu, ADR 0062). ``Project`` należy
    do tej powierzchni: jego akcja ``status`` zwraca syntezę projektu z notatek pionu.
    Zostawiony w katalogu bazowym był drogą OBOK bramki — jedyną, która przeżyła jej wpięcie.
    Domyślne ``False`` = zachowanie sprzed ADR 0062.
    """
    from sufler.adapters.outbound.anthropic_llm import AnthropicLLMClient

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
    # ``Project`` zamiast ``get_project_status`` i ``save_note``, a trzy narzędzia odczytu bazy
    # wiedzy warunkowo — zastępuje je powłoka, której przy wyłączonej bramce po prostu nie ma.
    catalog = (
        []
        if suppress_notes_read
        else [
            *build_project_catalog(projects_service, write_service=write_service),
            *(
                []
                if shell_available
                else build_agent_notes_read_catalog(notes_service, projects_service)
            ),
        ]
    )
    return AgentRuntime(
        AnthropicLLMClient(agent_settings),
        [*catalog, *extra_catalog],
        system_prompt=(
            system_prompt
            if system_prompt is not None
            else static_prompt_for(attachments=False, shell=shell_available)
        ),
        max_tool_iterations=agent_settings.max_tool_iterations,
    )


def build_agent_runtime_or_exit(
    settings: Settings,
    agent_settings: AgentSettings,
    *,
    enable_write: bool,
    extra_catalog: Sequence[ToolSpec] = (),
    system_prompt: str | None = None,
    shell_available: bool = False,
    suppress_notes_read: bool = False,
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
            suppress_notes_read=suppress_notes_read,
        )
    except ImportError as exc:
        raise SystemExit(_MISSING_AGENT) from exc


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
    github_thread_link: Callable[[str], tuple[str, int] | None] | None = None,
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
    note_read_authorizer: NoteReadAuthorizer | None = None,
    shell_authorizer: ShellAuthorizer | None = None,
    enable_file_tool: bool = False,
    file_tool_budget_bytes: int = 0,
    file_tool_max_image_edge: int = 2048,
    file_tool_staged_ext: frozenset[str] = frozenset(),
    file_tool_limits: MaterializationLimits = _BEZ_PULAPOW,
    trust_labels: bool = False,
    enable_note_mutation: bool = False,
    enable_note_delete: bool = False,
    identities: AadIdentityLookup | None = None,
) -> Responder:
    """Złóż całą receptę drzwi: runtime → store → pamięć → kompaktowanie → router komend.

    Jedno źródło recepty ``SafeResponder(ConversationalResponder(...))`` (dawniej skopiowanej
    w 4 drzwiach). ``safe=True`` owija w ``SafeResponder`` (drzwi async); ``show_thinking`` tylko
    dla drzwi zaufanych (CLI). Router komend dostaje katalog READ-ONLY (bramka ADR 0006).
    ``enable_workspace`` (osobna bramka, ADR 0018) dokłada agentowi narzędzia katalogu roboczego,
    a ``shell_settings.enabled`` (znów osobna, ADR 0057) — narzędzie ``Bash`` biegnące
    w kontenerze-wykonawcy bez sieci. Te dwie bramki są od kroku 5.5 (ADR 0009 paczki)
    ROZŁĄCZNE w skutku: z powłoką narzędzia plikowe nie wchodzą, bo `Bash` startuje w tym samym
    katalogu i robi to samo — patrz komentarz przy ``workspace_factory``.
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
        _build_shell_factory(
            shell_settings,
            workspace_settings,
            outbox_enabled=outbox_enabled,
            authorizer=shell_authorizer,
        )
        if shell_settings is not None and workspace_settings is not None
        else None
    )
    # Etap 7 (ADR 0011 paczki): ``ReplyWithFile`` — szóste narzędzie — schodzi z powierzchni
    # tam, gdzie jest powłoka. Dostawa pliku idzie wtedy skrzynką ``outputs/`` (``outbox_delivery``
    # niżej), a narzędzie byłoby DRUGĄ drogą do tego samego — trzy pozycje budżetu wyboru za
    # zdolność, którą już mamy. Bez powłoki ``ReplyWithFile`` zostaje JEDYNĄ drogą dostawy, więc
    # zostaje. Warunek z ``shell_factory``, nie z ``shell_settings.enabled`` — jak przy narzędziach
    # plikowych i skillach: ustawienie mówi, czego chce operator, fabryka — co agent dostanie
    # (rozjazd na platformie bez wykonawcy zostawiłby agenta bez powłoki I bez ``ReplyWithFile``).
    if shell_factory is not None:
        thread_tool_factory = None
    # Autoryzacja ODCZYTU (ADR 0062): bramka działa na TYPOWANYCH ścieżkach. Gdy działa,
    # powierzchnia bazy wiedzy schodzi z katalogu BAZOWEGO (``suppress``) do per-turowej fabryki
    # bramkowanej nadawcą. Komenda ``/szukaj``/``/projekty`` dostaje authorizer niezależnie
    # od powłoki (osobna ścieżka odczytu). ``None`` → wszystko jak przed ADR 0062.
    #
    # WARUNEK TO SAMA OBECNOŚĆ AUTORYZATORA. Poprzedni zapis niósł dodatkowo ``shell_factory is
    # None`` i uzasadniał to zdaniem „z powłoką narzędzi odczytu i tak nie ma". Zdanie jest
    # nieprawdziwe dla ``Project``: trójkę odczytu istotnie zdejmuje ``shell_available``, ale
    # ``build_project_catalog`` nie zależy od niego wcale, więc przy ``ENABLE_SHELL=true``
    # ``Project`` ZOSTAWAŁ w katalogu bazowym — poza bramką. A to on serwuje treść
    # (``Project(action='status')`` zwraca syntezę z notatek pionu) i to jego ADR 0062 wciągnął
    # za bramkę jako „jedyną drogę odczytu, która przeżyła jej wpięcie".
    #
    # **Wada była UTAJONA, nie czynna, i to rozróżnienie jest tu treścią.** Na flocie
    # ``NOTE_READ_AUTHZ`` jest wyłączone, więc autoryzatora nie ma wcale i bramka nie działa
    # w ogóle — nie było czego omijać. Ale ``ENABLE_SHELL`` jest WŁĄCZONE, więc wada zapaliłaby
    # się w chwili włączenia flagi: operator dostałby bramkę, która melduje włączenie i nie
    # obejmuje narzędzia serwującego treść. To jest powód, dla którego ta poprawka jest
    # WARUNKIEM włączenia flagi (krok 6.2 karty aktywacji), a nie naprawą trwającego wycieku.
    #
    # To ten sam błąd, co przy rozszczepieniu T1/T2 niżej, naprawiony w tym pliku po raz drugi:
    # dwa niezależne warunki splątane w jednej nazwie. Stąd dwie nazwy zamiast jednej.
    bramka_odczytu_dziala = note_read_authorizer is not None
    notes_read_factory = (
        _build_notes_read_factory(
            settings,
            note_read_authorizer,
            enable_write=enable_write,
            shell_available=shell_factory is not None,
        )
        if note_read_authorizer is not None
        else None
    )
    # Bramka MUTACJI bazy wiedzy (ADR 0065). Dwa warunki i oba są konieczne: przełącznik
    # operatora oraz mapa tożsamości — bez niej nie ma komu przypisać zmiany ani kogo zapytać
    # o potwierdzenie. Kasowanie ma WŁASNY przełącznik, bo ADR wiąże je z działającą kopią
    # zapasową, a to fakt o infrastrukturze, nie o kodzie.
    #
    # ``enable_write`` (profil zapisu drzwi, ADR 0006) NIE jest tu warunkiem i to jest
    # świadome: właściciel wybrał dla mutacji osobny kanał (`File`), a nie rozszerzenie
    # `Notes(save)`. Drzwi Teams mają `enable_write=False` i mimo to mogą — po włączeniu tej
    # bramki — zmieniać notatki. To dwie różne zdolności za dwoma różnymi przełącznikami,
    # nie przeoczenie.
    mutations = None
    if enable_note_mutation and identities is not None:
        notes_repo = MarkdownNotesRepository(settings.notes_dir)
        mutations = NoteMutationService(
            notes_repo,
            MarkdownNotesWriter(settings.notes_dir),
            FilesystemNoteSnapshots(settings.note_snapshots_dir),
            AnthropicMutationJudge(agent_settings),
            InMemoryConfirmations(),
            allow_delete=enable_note_delete,
        )
    file_factory = None
    attachment_stager = None
    if (
        enable_file_tool
        and supports_attachments
        and workspace_settings is not None
        and file_tool_budget_bytes > 0
    ):
        file_factory, attachment_stager = build_file_support(
            workspace_settings,
            max_image_edge=file_tool_max_image_edge,
            staged_ext=file_tool_staged_ext,
            materialization_limits=file_tool_limits,
            mutations=mutations,
            identities=identities,
            read_authorizer=note_read_authorizer,
            # Z FABRYKI, jak `shell_available` runtime'u niżej: ustawienie mówi, czego chciał
            # operator, fabryka — co agent faktycznie dostanie.
            shell_available=shell_factory is not None,
        )
    runtime = build_agent_runtime_or_exit(
        settings,
        agent_settings,
        enable_write=enable_write,
        extra_catalog=extra_catalog,
        # Wariant `ENVIRONMENT` z TEJ SAMEJ fabryki co katalog narzędzi (etap 6). Gdyby brał się
        # z `shell_settings.enabled`, opis świata i katalog rozjechałyby się dokładnie tam, gdzie
        # rozjeżdża się ustawienie z fabryką: bez `workspace_settings` i na platformie, gdzie
        # klient wykonawcy się nie importuje.
        system_prompt=static_prompt_for(
            attachments=supports_attachments,
            shell=shell_factory is not None,
            # Wariant zdania o notatkach z TEGO SAMEGO źródła co bramka mutacji (ADR 0065),
            # dokładnie jak wariant `ENVIRONMENT` z fabryki powłoki. Rozjazd dałby prefiks
            # mówiący „nie zmieniaj notatek" obok narzędzia, które właśnie to umie — czyli
            # albo martwe narzędzie, albo cicho fałszywy prompt.
            # Z FAKTYCZNEJ dostępności, nie z samej bramki: `File` wymaga jeszcze własnego
            # przełącznika, materializera i katalogu roboczego. Przy `ENABLE_NOTE_MUTATION`
            # i wyłączonym `ENABLE_FILE_TOOL` prompt obiecywałby zmienianie notatek bez
            # narzędzia, które to robi — czyli świat SZERSZY niż faktyczny, w stronę, którą
            # docstring `static_prompt_for` nazywa gorszą.
            mutation=mutations is not None and file_factory is not None,
        ),
        # Z FABRYKI, nie z ustawień. `shell_settings.enabled` mówi, czego chciał operator;
        # `shell_factory` — co agent faktycznie dostanie. Rozjeżdżają się przy braku
        # `workspace_settings` i na platformie, gdzie klient wykonawcy nie importuje się
        # (POSIX-only). Rozjazd oznaczałby agenta bez powłoki I bez narzędzi odczytu, czyli
        # bez jakiejkolwiek drogi do bazy wiedzy — po cichu.
        shell_available=shell_factory is not None,
        # Z bramką odczytu (ADR 0062) całą powierzchnię bazy wiedzy przejmuje fabryka per turę —
        # także przy włączonej powłoce, bo ``Project`` serwuje treść niezależnie od niej.
        suppress_notes_read=bramka_odczytu_dziala,
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
        note_read_authorizer=note_read_authorizer,
    )
    # Narzędzia plikowe katalogu roboczego wchodzą TYLKO tam, gdzie nie ma powłoki (ADR 0009
    # paczki, krok 5.5). Z powłoką są czystym opakowaniem prymitywu: `Bash` startuje w TYM SAMYM
    # katalogu, więc `cat`/`ls`/heredoc robią dokładnie to samo, za trzy pozycje w budżecie wyboru.
    #
    # Warunek, a nie bezwarunkowe cięcie — z tego samego powodu co przy narzędziach odczytu
    # notatek (`build_agent_notes_read_catalog`): `SUFLER_ENABLE_SHELL` jest domyślnie
    # WYŁĄCZONA. Bez powłoki bariera istnieje i jest węższa, niż wygląda: to NIE jest droga do
    # dostawy pliku (`ReplyWithFile` bierze treść wprost, a skrzynka nadawcza czyta `outputs/`,
    # dokąd `CreateFile` nie umie zapisać), tylko jedyny sposób, w jaki model odzyskuje własny
    # szkic po kompaktowaniu kontekstu (ADR 0014) — wtedy tura, w której go pisał, nie wraca.
    #
    # Warunek liczymy z `shell_factory`, nie z `shell_settings.enabled` — z tego samego powodu co
    # `shell_available` niżej: ustawienie mówi, czego chciał operator, fabryka mówi, co agent
    # faktycznie dostanie. Rozjazd zostawiłby agenta bez powłoki I bez narzędzi plikowych.
    workspace_factory = (
        _build_workspace_factory(workspace_settings)
        if enable_workspace and workspace_settings is not None and shell_factory is None
        else None
    )
    # Narzędzie ``File`` i odkładanie załączników (ADR 0064). Bramka jest inna niż przy narzędziach
    # katalogu roboczego: te ostatnie znikają, gdy jest powłoka (bo `cat` robi to samo), a ``File``
    # zostaje w OBU układach — wstawienia obrazu czy PDF do kontekstu powłoka nie zrobi, bo zwraca
    # tekst. Warunkiem jest natomiast to, żeby drzwi w ogóle MATERIALIZOWAŁY załączniki
    # (``supports_attachments``) i miały katalog roboczy: bez jednego nie ma czego odkładać, bez
    # drugiego nie ma gdzie. Budżet 0 = brak narzędzia (operator nie podał sufitu → nie obiecujemy).
    # Licznik wywołań (Tor A): włączony obecnością SUFLER_METRICS_DB; ``None`` → wyłączony,
    # responder nie zapisuje nic. Jeden punkt wpięcia obejmuje wszystkie drzwi agentowe.
    metrics = (
        MetricsService(SqliteMetricsStore(settings.metrics_db))
        if settings.metrics_db is not None
        else None
    )
    # Dziennik audytu (Faza 0, ADR 0067): włączony obecnością SUFLER_AUDIT_DB; ``None`` →
    # wyłączony, runtime nie dostaje rejestratora i nie zapisuje nic. Jeden punkt wpięcia (per turę,
    # w responderze) obejmuje wszystkie drzwi agentowe; drzwi MCP są poza szwem (ADR 0067 R7).
    audit = (
        AuditService(SqliteAuditStore(settings.audit_db)) if settings.audit_db is not None else None
    )
    # Procedury z `/mnt/skills` (ADR 0005) — odczyt RAZ przy składaniu drzwi. Brak katalogu daje
    # pustą listę i zachowanie dokładnie dawne; nagłówek sesji nie dostaje wtedy sekcji skilli.
    #
    # Warunek na `shell_factory` doszedł w etapie 6 i zamyka martwą obietnicę tej samej klasy co
    # `/mnt/user/outputs`. Nagłówek mówi „read the one that fits before starting", a jedyną drogą
    # do treści procedury jest `cat` w wykonawcy: narzędzia plikowe katalogu roboczego są zamknięte
    # w scope'ie rozmowy i `/mnt/skills` nie widzą. Bez powłoki model dostawał więc listę nazw
    # i polecenie przeczytania czegoś, po co nie ma jak sięgnąć.
    skills = (
        read_skill_catalog(skills_settings.skills_dir, limit=skills_settings.max_in_header)
        if skills_settings is not None and shell_factory is not None
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
        # Etykiety T3 (ADR 0066) — niezależne od rozszczepienia nadawcy: nie zależą od mapy
        # tożsamości i nikogo nie degradują, więc mają własną bramkę.
        trust_labels=trust_labels,
        # Rozszczepienie T1/T2 jedzie za bramką 0062, bo obie zależą od kompletności mapy:
        # ten sam autoryzator, jedno rozwiązanie tożsamości na turę (ADR 0066 R4).
        #
        # Warunek to SAMA obecność autoryzatora. Historycznie stała tu obrona przed
        # ``notes_read_gated``, który niósł dodatkowo ``shell_factory is None`` — pod nim
        # rozszczepienie WYGASAŁO po cichu przy włączonej powłoce, czyli dokładnie w układzie
        # docelowym, a tekst gościa wracał do rangi instrukcji i audyt notował „unknown".
        #
        # Ta pułapka zniknęła u ŹRÓDŁA: warunek bramki odczytu to dziś ``bramka_odczytu_dziala``
        # i też jest samą obecnością autoryzatora (patrz wyżej — ten sam błąd naprawiony w tym
        # pliku po raz drugi, bo za pierwszym razem naprawiono go tylko tutaj, a nie tam, gdzie
        # powstawał). Zdanie zostaje jako zapis, dlaczego oba warunki brzmią tak, a nie inaczej.
        sender_trust=(
            note_read_authorizer.trust_class if note_read_authorizer is not None else None
        ),
        file_catalog_factory=file_factory,
        attachment_stager=attachment_stager,
        attachment_budget_bytes=file_tool_budget_bytes,
        thread_tool_factory=thread_tool_factory,
        user_push_tool_factory=user_push_tool_factory,
        my_jira_tasks_factory=my_jira_tasks_factory,
        notes_read_factory=notes_read_factory,
        github_thread_link=github_thread_link,
        meeting_notes=meeting_notes,
        thread_note=thread_note,
        project_brief=project_brief,
        change_digest=change_digest,
        metrics=metrics,
        audit=audit,
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
    from sufler.adapters.outbound.anthropic_llm import AnthropicLLMClient

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
