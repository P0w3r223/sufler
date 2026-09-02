"""Entry point drzwi Teams w trybie DELEGOWANYM (ADR 0015) — polling kanału przez Graph.

Bot odpowiada RUNTIME AGENTA rdzenia (jak inne drzwi botowe), ale działa jako
ZALOGOWANY UŻYTKOWNIK: bez publicznego endpointu i bez rejestracji bota. Katalog narzędzi
jest READ-ONLY (ADR 0006) — agent czyta notatki i status, nie zapisuje. Pamięć rozmów jest
PER WĄTEK kanału (``channel="teams_graph"``, ``conversation_id="team/channel/root"``).

Uruchomienie: ``uv run workmate-teams-graph`` (wymaga ``uv sync --extra teams-graph
--extra agent`` oraz ``ANTHROPIC_API_KEY``). Bez ``WORKMATE_TEAMS_GRAPH_WATCH`` proces
wypisze dostępne zespoły i kanały z ich ID i zakończy działanie.

Importy ``msal``/``httpx``/``anthropic`` są leniwe; brak extra kończy się czytelnym
komunikatem, nie surowym ``ImportError``. Powrót do samego echa (bez API/klucza) to jedna
linia: ``RuntimeResponder`` → ``EchoResponder`` (patrz ``adapters/inbound/responder/simple.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from workmate.adapters.inbound import env
from workmate.adapters.inbound.agent_wiring import build_conversational_responder
from workmate.adapters.inbound.document_text import SUPPORTED_EXTS
from workmate.adapters.inbound.teams_graph.handler import make_handle_message
from workmate.adapters.outbound.filesystem_workspace import prune_stale
from workmate.config import (
    AgentSettings,
    ConversationSettings,
    EventsSettings,
    GithubSettings,
    JiraSettings,
    ScheduleSettings,
    Settings,
    ShellSettings,
    SkillsSettings,
    TeamsGraphSettings,
    WorkspaceSettings,
    require_writable,
)
from workmate.core.errors import LLMError
from workmate.core.ports.materialization import MaterializationLimits

if TYPE_CHECKING:
    from workmate.adapters.inbound.brief_command import BriefRouter
    from workmate.adapters.inbound.change_command import ChangeDigestRouter
    from workmate.adapters.inbound.meeting_command import MeetingNoteRouter
    from workmate.adapters.inbound.responder import Responder
    from workmate.adapters.inbound.teams_graph.poller import HandleMessage, MessageDeadLetterStore
    from workmate.adapters.inbound.thread_note_command import ThreadNoteRouter
    from workmate.core.application.note_read_authz import NoteReadAuthorizer
    from workmate.core.application.shell_authz import ShellAuthorizer
    from workmate.core.application.tools import ToolSpec
    from workmate.core.ports.outbox import Deliverable

from workmate.adapters.inbound.teams_graph.wiring_authz import (
    _build_mutation_identities,
    _build_note_read_authorizer,
    _build_shell_authorizer,
)
from workmate.adapters.inbound.teams_graph.wiring_bridge import (
    _build_bridge_catalog,
    _make_thread_link_lookup,
    _worklog_service,
)
from workmate.adapters.inbound.teams_graph.wiring_catalogs import (
    _build_my_jira_tasks_factory,
    _build_team_schedule_catalog,
)
from workmate.adapters.inbound.teams_graph.wiring_common import _MISSING_TEAMS_GRAPH
from workmate.adapters.inbound.teams_graph.wiring_delivery import (
    _build_file_reply_factory,
    _build_outbox_send_factory,
    _build_thread_pdf_delivery,
    _build_user_doc_push_factory,
    _build_user_push_factory,
    _compose_user_push_factories,
)
from workmate.adapters.inbound.teams_graph.wiring_routers import (
    _build_brief_router,
    _build_change_digest_router,
    _build_meeting_note_router,
    _build_thread_note_router,
)

# Fabryki wiringu mieszkają dziś w modułach ``wiring_*``; te drzwi zostają ICH JEDYNYM
# publicznym adresem. Testy wiringu (siedem plików w ``tests/adapters/inbound/teams_graph/``)
# importują je stąd i po rozbiciu miały działać BEZ ZMIANY — dlatego ``_make_thread_link_lookup``
# i ``_worklog_service`` stoją w ``__all__`` mimo że samo ``app`` ich nie woła. Bez tego wpisu
# ruff usunąłby je jako nieużywane, a testy padłyby na imporcie.
__all__ = [
    "_build_bridge_catalog",
    "_build_file_reply_factory",
    "_build_meeting_note_router",
    "_build_my_jira_tasks_factory",
    "_build_shell_authorizer",
    "_build_team_schedule_catalog",
    "_build_user_doc_push_factory",
    "_build_user_push_factory",
    "_compose_user_push_factories",
    "_make_thread_link_lookup",
    "_worklog_service",
    "main",
]

logger = logging.getLogger(__name__)

# Rozszerzenia załączników odkładanych na dysk katalogu rozmowy (ADR 0064): wszystko, co drzwi
# umieją zamienić na tekst, plus formaty, które Claude API przyjmuje natywnie (obrazy i PDF).
# Lista jest jawna, a nie „cokolwiek przyszło": nazwa pliku pochodzi od użytkownika, a katalog
# roboczy dzieli korzeń z powłoką — plik z rozszerzeniem wykonywalnym nie ma po co tam leżeć.
_STAGED_ATTACHMENT_EXTS = SUPPORTED_EXTS | frozenset(
    {"png", "jpg", "jpeg", "gif", "webp", "heic", "heif"}
)


def main() -> None:
    """Uruchom proces drzwi Teams (delegowany polling) z runtime agenta (read-only)."""
    env.load_dotenv()
    env.configure_logging()

    settings = TeamsGraphSettings.from_env()
    settings.validate()
    # R/L1: stan wątków i cache MSAL (refresh-token) na wolumenie MUSZĄ być zapisywalne — fail-fast,
    # nim ruszy odkrywanie/polling (inaczej cache device-code przepada i logujemy się w kółko).
    require_writable(settings.state_path, "WORKMATE_TEAMS_GRAPH_STATE")
    require_writable(settings.token_cache_path, "WORKMATE_TEAMS_GRAPH_TOKEN_CACHE")

    try:
        from workmate.adapters.inbound.teams_graph.auth import build_token_provider
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc
    token_provider = build_token_provider(settings)

    # Bez WATCH: tryb odkrywania — wypisz zespoły/kanały i zakończ. Nie buduje runtime'u
    # agenta, więc NIE wymaga ANTHROPIC_API_KEY (walidację klucza robimy dopiero niżej).
    if not settings.watch:
        logger.info("Brak WORKMATE_TEAMS_GRAPH_WATCH — wypisuję dostępne zespoły i kanały.")
        asyncio.run(_discover(settings, token_provider))
        return

    core_settings = Settings.from_env()
    agent_settings = AgentSettings.from_env()
    agent_settings.validate()
    conv_settings = ConversationSettings.from_env()
    conv_settings.validate()
    workspace_settings = WorkspaceSettings.from_env()
    workspace_settings.validate(
        data_dir=core_settings.data_dir,
        persistent_paths=core_settings.persistent_paths(),
    )
    shell_settings = ShellSettings.from_env()
    shell_settings.validate()
    # Sufit listy procedur sprawdzany PRZY STARCIE, nie przy składaniu nagłówka: zła wartość
    # ukrywa procedury bez śladu, więc ma wywrócić start, zamiast wyglądać jak pusty katalog.
    skills_settings = SkillsSettings.from_env()
    skills_settings.validate()
    # Bramka członkostwa POWŁOKI (ADR 0063), osobno — ``None`` gdy powłoka wyłączona. Gdy włączona,
    # WYMAGA mapy tożsamości (fail-fast w builderze), więc rozstrzygamy ją WCZEŚNIE: brak mapy ma
    # wywrócić start, zanim ruszymy resztę składania drzwi.
    shell_authorizer = _build_shell_authorizer(settings, shell_settings)
    # R/L1: pamięć rozmów agenta i wspólny events.db MUSZĄ być zapisywalne (tryb watch pisze oba).
    events_settings = EventsSettings.from_env()
    events_settings.validate(data_dir=core_settings.data_dir)
    require_writable(events_settings.db_path, "WORKMATE_EVENTS_DB")
    require_writable(conv_settings.db_path, "WORKMATE_CONVERSATIONS_DB")
    # Baza wiedzy — sondowana TYLKO przy włączonym zapisie notatek (inaczej drzwi read-only
    # wywracałyby się na katalogu, którego nigdy nie tkną). Bez tej sondy montaż read-only
    # ujawnia się dopiero wyjątkiem w wątku tła (zapis async, ADR 0043) — już PO tym, jak nadawca
    # dostał potwierdzenie przyjęcia komendy. Katalog, nie plik w nim: wolumen bazy wiedzy bywa
    # montowany osobno od reszty stanu, więc sonda o poziom wyżej przepuściłaby read-only
    # (ADR 0008 tej paczki wdrożeniowej).
    if settings.enable_meeting_note_write or settings.enable_thread_note_capture:
        require_writable(core_settings.notes_dir, "WORKMATE_NOTES_DIR", is_directory=True)
    # R/L1 dla ścieżek, które ustawienia rdzenia piszą, a drzwi dotąd sondowały wyrywkowo:
    # migawki notatek (ADR 0065) oraz opcjonalne bazy metryk i audytu. Lista pochodzi z JEDNEGO
    # miejsca (``Settings.persistent_paths``), bo rozsypana po drzwiach gubiła pozycje: migawka
    # domyślnie ląduje pod montażem read-only floty, a rejestrator audytu łyka błędy per
    # wywołanie — operator dostawał „dziennik" z zerem wierszy zamiast błędu startu.
    #
    # Migawki mają WARUNEK, dokładnie jak baza wiedzy trzy linie wyżej i z tego samego powodu:
    # domyślna ścieżka to ``data_dir/snapshots/notes``, a ``data`` jest na flocie montowane ``:ro``
    # (``deploy/docker/docker-compose.yml``), przy czym ``env.example`` nie przekierowuje jej
    # nigdzie indziej. Bezwarunkowa sonda kładłaby więc drzwi na szablonie domyślnym — przy
    # WYŁĄCZONEJ mutacji notatek, czyli na katalogu, którego proces nigdy nie tknie.
    # Bazy metryk i audytu warunku nie mają i mieć nie powinny: wchodzą na listę WYŁĄCZNIE wtedy,
    # gdy operator jawnie wskazał plik, a to jest już deklaracja „chcę tu pisać".
    for path, env_var, is_dir in core_settings.persistent_paths():
        if path == core_settings.note_snapshots_dir and not settings.enable_note_mutation:
            continue
        require_writable(path, env_var, is_directory=is_dir)
    # TTL sprzątanie katalogu roboczego (ADR 0018) — raz na starcie, backstop przeciw rośnięciu.
    #
    # Warunkiem jest ISTNIENIE katalogu, nie bramka ``WORKMATE_ENABLE_WORKSPACE``. Retencja jest
    # własnością DANYCH, a nie tego, które narzędzia są włączone — a te dwie rzeczy rozjechały się
    # w układzie docelowym: przy powłoce ON i workspace OFF do brudnopisu pisze WYKONAWCA, a
    # bramka narzędzi jest zamknięta, więc sprzątacz nie biegł ani razu. Zmierzone na produkcji
    # 2026-08-18: katalogi rozmów z próby 11–12.08 leżały nietknięte, choć TTL wynosi 30 dni.
    #
    # ``prune_stale`` na nieistniejącym korzeniu jest ciche i zwraca 0, więc bezwarunkowe wołanie
    # nie robi nic tam, gdzie brudnopisu nie ma.
    #
    # Zostaje ograniczenie, którego ta zmiana NIE zdejmuje: sprzątanie pada raz, przy starcie
    # drzwi. Proces żyjący tygodniami nie posprząta w międzyczasie — domknięcie tego wymaga
    # zegara po stronie menedżera wykonawców i jest osobną decyzją (plan, §7.1 wariant (c)).
    removed = prune_stale(
        workspace_settings.workspace_dir,
        older_than=timedelta(days=workspace_settings.retention_days),
        now=datetime.now(tz=UTC),
    )
    # Ślad zostaje TAKŻE przy zerze — i to jest wniosek z naprawianego właśnie błędu. Przez ponad
    # 30 dni nie było żadnego sygnału, bo sprzątacz nie biegł; po tej zmianie „nie usunięto nic"
    # (korzeń pusty, wolumen niezamontowany, zła ścieżka) wyglądałoby w dzienniku identycznie.
    # Korzeń w komunikacie, bo to jedyna liczba, która odróżnia te przypadki.
    logger.info(
        "Katalog roboczy %s: usunięto %d bezczynnych katalogów rozmów (TTL %d dni).",
        workspace_settings.workspace_dir,
        removed,
        workspace_settings.retention_days,
    )
    jira_settings = JiraSettings.from_env()
    extra_catalog, github_thread_link = _build_bridge_catalog(
        events_settings, GithubSettings.from_env()
    )
    # Grafik Teams Shifts (ADR 0059): read-only, cichy token z cudzego cache MSAL. Katalog statyczny
    # — dokładany do extra_catalog tylko gdy włączony (istnieje mont cache); błędy tokenu/consentu
    # materializują się dopiero przy wywołaniu narzędzia (koperta), więc nie wywracają startu.
    schedule_settings = ScheduleSettings.from_env()
    schedule_settings.validate()
    extra_catalog.extend(_build_team_schedule_catalog(schedule_settings))
    # ADR 0026 (A′2): dokładamy fabrykę `ReplyWithFile`, niezależnie bramkowaną od zapisu GitHub.
    thread_factory = _build_file_reply_factory(settings, token_provider)
    # ADR 0027 (A′3): OSOBNE fabryki push-u 1:1 — klucz = nadawca, nie wątek. Obraz inline
    # (`SendImage`) i dokument (`SendDocument`) są niezależnie bramkowane
    # (dokument wymaga szerszego zakresu Files.ReadWrite.All), więc składamy je w jedną fabrykę.
    user_push_factory = _compose_user_push_factories(
        _build_user_push_factory(settings, token_provider),
        _build_user_doc_push_factory(settings, token_provider),
    )
    # "Moje zadania" Jira (ADR 0054): fabryka PER NADAWCA, niezależna od push-u — zasila zarówno
    # per-turowy katalog agenta, jak i komendę `/moje-zadania` (przez `_build_responder`).
    my_jira_tasks_factory = _build_my_jira_tasks_factory(settings, jira_settings)
    # Produkcyjne M3 (ADR 0009 §4 / 0041): komenda ZAPISU /notatka z drzwi Teams, osobno bramkowana.
    meeting_router = _build_meeting_note_router(
        settings, token_provider, core_settings, agent_settings
    )
    # Przechwycenie „zapisz to" z wątku (ADR 0048, F2): @wzmianka bota → notatka, osobno bramkowana.
    thread_router = _build_thread_note_router(
        settings, token_provider, core_settings, agent_settings
    )
    # Współdzielona dostawa PDF w wątku (ADR 0026) dla read-only jednostronicówek — jeden sender/
    # klient, budowany tylko gdy brief (F4) lub digest (F5) jest włączony.
    thread_pdf = (
        _build_thread_pdf_delivery(settings, token_provider)
        if settings.enable_project_brief or settings.enable_change_digest
        else None
    )
    # One-pager „ogarnij mnie na <projekt>" (ADR 0051, F4) i digest „co się zmieniło od <data>"
    # (ADR 0052, F5): @wzmianka bota → read-only jednostronicówka, każda osobno bramkowana.
    # Autoryzacja ODCZYTU bazy wiedzy (ADR 0062), osobno bramkowana — ``None`` gdy wyłączona.
    # Rozstrzygana PRZED routerami dyrektyw, bo obie (brief, digest) jadą za tą samą bramką:
    # odpalały się przed jakąkolwiek autoryzacją, więc @wzmianka była drogą OBOK niej.
    note_read_authorizer = _build_note_read_authorizer(settings)
    brief_router = _build_brief_router(
        settings, core_settings, events_settings, thread_pdf, note_read_authorizer
    )
    change_router = _build_change_digest_router(
        settings, events_settings, thread_pdf, note_read_authorizer
    )
    responder = _build_responder(
        core_settings,
        agent_settings,
        conv_settings,
        workspace_settings,
        shell_settings,
        extra_catalog,
        thread_factory,
        github_thread_link,
        user_push_factory,
        my_jira_tasks_factory,
        meeting_router,
        thread_router,
        brief_router,
        change_router,
        # Skrzynka nadawcza rozmowy (ADR 0009 paczki) — dzieli bramkę i limit z `ReplyWithFile`.
        _build_outbox_send_factory(settings, token_provider),
        settings.max_file_reply_kb * 1024,
        settings.outbox_max_files_per_turn,
        settings.outbox_max_seconds,
        # Procedury z `/mnt/skills` (ADR 0005) — bez ścieżki lista zostaje pusta.
        skills_settings,
        note_read_authorizer=note_read_authorizer,
        shell_authorizer=shell_authorizer,
        # Narzędzie ``File`` (ADR 0064) dzieli sufit z materializerem załączników, bo pobrania
        # modelu i pliki użytkownika lecą w TYM SAMYM żądaniu API — dwa niezależne budżety
        # sumowałyby się ponad limit żądania. Stąd te same ustawienia, nie nowe.
        enable_file_tool=settings.enable_file_tool,
        enable_note_mutation=settings.enable_note_mutation,
        enable_note_delete=settings.enable_note_delete,
        mutation_identities=_build_mutation_identities(settings),
        attachment_budget_bytes=settings.max_total_attachment_mb * 1024 * 1024,
        attachment_max_image_edge=settings.max_image_edge_px,
        attachment_max_bytes=settings.max_attachment_mb * 1024 * 1024,
        attachment_max_extract_bytes=settings.max_extract_mb * 1024 * 1024,
        trust_labels=settings.enable_trust_labels,
    )
    handle = make_handle_message(responder)
    # Kwarantanna wiadomości porzuconych po wyczerpaniu prób (ADR 0069) — ta sama baza i ten sam
    # wolumen co dead-letter notifiera (ADR 0067 §2), bo to metadane dostawy po drugiej stronie
    # mostu. Zapisywalność ``events.db`` jest sprawdzona wyżej (``require_writable``).
    from workmate.adapters.outbound.sqlite_dead_letters import SqliteInboundDeadLetterStore

    asyncio.run(
        _run(
            settings,
            token_provider,
            handle,
            dead_letters=SqliteInboundDeadLetterStore(events_settings.db_path),
        )
    )


def _build_responder(
    core_settings: Settings,
    agent_settings: AgentSettings,
    conv_settings: ConversationSettings,
    workspace_settings: WorkspaceSettings,
    shell_settings: ShellSettings,
    extra_catalog: list[ToolSpec],
    thread_factory: Callable[[str], list[ToolSpec]] | None = None,
    github_thread_link: Callable[[str], tuple[str, int] | None] | None = None,
    user_push_factory: Callable[[str], list[ToolSpec]] | None = None,
    my_jira_tasks_factory: Callable[[str], list[ToolSpec]] | None = None,
    meeting_router: MeetingNoteRouter | None = None,
    thread_router: ThreadNoteRouter | None = None,
    brief_router: BriefRouter | None = None,
    change_router: ChangeDigestRouter | None = None,
    outbox_send_factory: Callable[[str], Callable[[Deliverable], None] | None] | None = None,
    outbox_max_file_bytes: int = 0,
    outbox_max_files_per_turn: int = 5,
    outbox_max_seconds: float = 20.0,
    skills_settings: SkillsSettings | None = None,
    note_read_authorizer: NoteReadAuthorizer | None = None,
    shell_authorizer: ShellAuthorizer | None = None,
    enable_file_tool: bool = False,
    enable_note_mutation: bool = False,
    enable_note_delete: bool = False,
    mutation_identities: object | None = None,
    attachment_budget_bytes: int = 0,
    attachment_max_image_edge: int = 2048,
    attachment_max_bytes: int = 0,
    attachment_max_extract_bytes: int = 0,
    trust_labels: bool = False,
) -> Responder:
    """Złóż respondera wspólnym builderem: katalog notatek READ-ONLY (``enable_write=False``,
    ADR 0006), ``SafeResponder`` (async), komendy read-only, kompaktowanie. Katalog roboczy
    (ADR 0018) włącza OSOBNA bramka ``enable_workspace`` (env ``WORKMATE_ENABLE_WORKSPACE``),
    niezależna od zapisu notatek; powłokę (ADR 0057) — jeszcze inna, ``WORKMATE_ENABLE_SHELL``,
    bo tam model uruchamia dowolny kod, a nie tworzy plik narzędziem typowanym.
    ``extra_catalog`` (ADR 0019/0021) dokłada narzędzia warstwy
    spajającej, ``thread_factory`` (ADR 0026) — per-turowe ``ReplyWithFile``,
    ``github_thread_link`` (ADR 0024, Faza 3b) — powiązanie wątku z issue/PR do NAGŁÓWKA SESJI
    (dawniej osobne narzędzie ``reply_on_thread``, zniesione w kroku 5.5), a
    ``user_push_factory`` (ADR 0027, A′3) — per-turowe ``SendImage`` (obraz inline) oraz
    ``SendDocument`` (plik-załącznik) wiązane z nadawcą, niezależnie bramkowane.
    ``my_jira_tasks_factory`` (ADR 0054) — per-turowe ``Jira(action=…)`` wiązane z nadawcą,
    zasila też komendę ``/moje-zadania``. ``channel="teams_graph"`` trzyma pamięć/workspace tych
    drzwi osobno od bota."""
    return build_conversational_responder(
        core_settings,
        agent_settings,
        conv_settings,
        channel="teams_graph",
        enable_write=False,
        safe=True,
        # Błąd rozmowy z modelem ma dojść do licznika prób pollera (ADR 0069), a nie skończyć się
        # przeprosinami i wiadomością odhaczoną jako obsłużona. Te drzwi jako jedyne mają licznik,
        # więc jako jedyne o to proszą; reszta błędów dalej degraduje łagodnie w ``SafeResponder``.
        ponawialne=(LLMError,),
        enable_workspace=workspace_settings.enabled,
        workspace_settings=workspace_settings,
        shell_settings=shell_settings,
        extra_catalog=extra_catalog,
        thread_tool_factory=thread_factory,
        github_thread_link=github_thread_link,
        user_push_tool_factory=user_push_factory,
        my_jira_tasks_factory=my_jira_tasks_factory,
        meeting_notes=meeting_router,
        thread_note=thread_router,
        project_brief=brief_router,
        change_digest=change_router,
        supports_attachments=True,  # jedyne drzwi z materializerem załączników (F8/ADR 0016)
        outbox_send_factory=outbox_send_factory,
        outbox_max_file_bytes=outbox_max_file_bytes,
        outbox_max_files_per_turn=outbox_max_files_per_turn,
        outbox_max_seconds=outbox_max_seconds,
        skills_settings=skills_settings,
        note_read_authorizer=note_read_authorizer,
        shell_authorizer=shell_authorizer,
        # Narzędzie ``File`` (ADR 0064) dzieli sufit z materializerem drzwi, bo pobrania modelu i
        # załączniki użytkownika lecą w TYM SAMYM żądaniu API — dwa niezależne budżety sumowałyby
        # się do przekroczenia limitu żądania. Stąd te same ustawienia, nie nowe.
        enable_file_tool=enable_file_tool,
        file_tool_budget_bytes=attachment_budget_bytes,
        file_tool_max_image_edge=attachment_max_image_edge,
        file_tool_limits=MaterializationLimits(
            max_bytes=attachment_max_bytes,
            max_extract_bytes=attachment_max_extract_bytes,
        ),
        # Rozszerzenia, które wolno ODŁOŻYĆ na dysk rozmowy: to, co drzwi w ogóle materializują.
        # Szersze niż lista formatów, które model wolno mu TWORZYĆ (``workspace_settings``) —
        # odkładamy cudzy plik do wglądu, nie pozwalamy modelowi pisać binariów.
        file_tool_staged_ext=_STAGED_ATTACHMENT_EXTS,
        # Koperty T3 (ADR 0066) — bramka niezależna od rozszczepienia nadawcy.
        trust_labels=trust_labels,
        # Mutacja bazy wiedzy (ADR 0065) — trzy niezależne bramki: edycja, kasowanie, mapa.
        enable_note_mutation=enable_note_mutation,
        enable_note_delete=enable_note_delete,
        identities=mutation_identities,  # type: ignore[arg-type]
    )


async def _run(
    settings: TeamsGraphSettings,
    token_provider: Callable[[], str],
    handle: HandleMessage,
    *,
    dead_letters: MessageDeadLetterStore | None = None,
) -> None:
    try:
        import httpx

        from workmate.adapters.inbound.teams_graph.graph import HttpxGraphChannelClient
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc
    from workmate.adapters.inbound.heartbeat import heartbeat_path, write_heartbeat
    from workmate.adapters.inbound.teams_graph import state as state_store
    from workmate.adapters.inbound.teams_graph.attachments import (
        AttachmentLimits,
        AttachmentMaterializer,
    )
    from workmate.adapters.inbound.teams_graph.poller import ChannelPoller
    from workmate.adapters.inbound.teams_graph.selection import ReplyPolicy

    initial_state = state_store.load(settings.state_path)
    # Puls żywotności (R5): siostra pliku stanu na wolumenie, odświeżana po każdej udanej rundzie.
    hb_path = heartbeat_path(settings.state_path)

    # Graceful shutdown (R1): SIGTERM/SIGINT → poller dokańcza rundę kanałów, zapisuje i wraca.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # Windows nie ma add_signal_handler — tam zamknięcie idzie przez KeyboardInterrupt (SIGINT).
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    async with httpx.AsyncClient(timeout=30) as http:
        client = HttpxGraphChannelClient(http, token_provider)
        materializer = AttachmentMaterializer(
            client,
            limits=AttachmentLimits(
                max_bytes=settings.max_attachment_mb * 1024 * 1024,
                max_count=settings.max_attachments_per_message,
                max_total_bytes=settings.max_total_attachment_mb * 1024 * 1024,
                max_extract_bytes=settings.max_extract_mb * 1024 * 1024,
                max_image_edge=settings.max_image_edge_px,
            ),
        )
        poller = ChannelPoller(
            client,
            handle,
            watch=settings.watch,
            state=initial_state,
            persist=lambda s: state_store.save(settings.state_path, s),
            top_roots=settings.top_roots,
            top_replies=settings.top_replies,
            poll_interval=settings.poll_interval_s,
            active_idle=timedelta(hours=settings.active_idle_hours),
            materializer=materializer,
            stop=stop,
            heartbeat=lambda: write_heartbeat(hb_path),
            policy=ReplyPolicy.from_settings(settings),
            dead_letters=dead_letters,
        )
        await poller.run()


async def _discover(settings: TeamsGraphSettings, token_provider: Callable[[], str]) -> None:
    """Wypisz zespoły i kanały użytkownika z ich ID (do ustawienia WATCH)."""
    try:
        import httpx

        from workmate.adapters.inbound.teams_graph.graph import HttpxGraphChannelClient
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc

    async with httpx.AsyncClient(timeout=30) as http:
        client = HttpxGraphChannelClient(http, token_provider)
        await client.refresh_auth()
        for team in await client.list_joined_teams():
            print(f"\nZespół: {team.get('displayName')}\n  team_id={team.get('id')}")
            for channel in await client.list_channels(team["id"]):
                name = channel.get("displayName") or "?"
                print(f"    Kanał: {name:<28} channel_id={channel.get('id')}")
    print('\nUstaw np.:  $env:WORKMATE_TEAMS_GRAPH_WATCH = "<team_id>:<channel_id>"')


if __name__ == "__main__":
    main()
