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
linia: ``RuntimeResponder`` → ``EchoResponder`` (patrz ``adapters/inbound/responder.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from html import escape
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

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
from workmate.core.ports.materialization import MaterializationLimits

if TYPE_CHECKING:
    from workmate.adapters.inbound.brief_command import BriefRouter
    from workmate.adapters.inbound.change_command import ChangeDigestRouter
    from workmate.adapters.inbound.meeting_command import MeetingNoteRouter
    from workmate.adapters.inbound.responder import Responder
    from workmate.adapters.inbound.teams_graph.poller import HandleMessage, MessageDeadLetterStore
    from workmate.adapters.inbound.thread_note_command import ThreadNoteRouter
    from workmate.adapters.outbound.github_api import HttpxGithubClient
    from workmate.core.application.events import EventService
    from workmate.core.application.note_read_authz import NoteReadAuthorizer
    from workmate.core.application.shell_authz import ShellAuthorizer
    from workmate.core.application.tools import ToolSpec
    from workmate.core.application.worklog import WorklogService
    from workmate.core.ports.github import GithubReadPort
    from workmate.core.ports.outbox import Deliverable
    from workmate.core.ports.thread_links import ThreadLinkStore

logger = logging.getLogger(__name__)

_MISSING_TEAMS_GRAPH = (
    "Drzwi Teams (delegowane) wymagają extra 'teams-graph'. Zainstaluj: uv sync --extra teams-graph"
)

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
    workspace_settings.validate(data_dir=core_settings.data_dir)
    shell_settings = ShellSettings.from_env()
    shell_settings.validate()
    # Bramka członkostwa POWŁOKI (ADR 0063), osobno — ``None`` gdy powłoka wyłączona. Gdy włączona,
    # WYMAGA mapy tożsamości (fail-fast w builderze), więc rozstrzygamy ją WCZEŚNIE: brak mapy ma
    # wywrócić start, zanim ruszymy resztę składania drzwi.
    shell_authorizer = _build_shell_authorizer(settings, shell_settings)
    # R/L1: pamięć rozmów agenta i wspólny events.db MUSZĄ być zapisywalne (tryb watch pisze oba).
    events_settings = EventsSettings.from_env()
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
        SkillsSettings.from_env(),
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


def _build_bridge_catalog(
    events_settings: EventsSettings,
    github_settings: GithubSettings,
) -> tuple[list[ToolSpec], Callable[[str], tuple[str, int] | None] | None]:
    """Narzędzia warstwy SPAJAJĄCEJ dla agenta Teams (ADR 0019/0021/0024): zdarzenia + zapis GitHub.

    Zwraca ``(katalog, odczyt_powiązania_wątku)``. ``Activity(action='events')`` jest ZAWSZE (agent
    widzi, co zdarzyło się w innych warstwach). Zapis do GitHub (issue/komentarz) dokładamy TYLKO
    przy włączonej bramce i skonfigurowanym celu — profil per drzwi (ADR 0006/0021). Zdarzenia
    z zapisu idą jako ``source=teams`` (strażnik pętli — notifier ich nie odeśle). Gdy zapis jest
    włączony, oddajemy też odczyt powiązania wątek↔issue — od kroku 5.5 (ADR 0009 paczki) idzie on
    do NAGŁÓWKA SESJI, a nie jako osobne narzędzie ``reply_on_thread``.

    Jira nie ma tu żadnej zdolności mutującej ani zdarzeń push (ADR 0054 zredukował ją do jednej,
    wyłącznie odczytowej funkcji — patrz ``_build_my_jira_tasks_factory``, per nadawca, poza tym
    katalogiem). Propozycja czasu z commitów (ADR 0034) wchodzi BEZ bramki, gdy tylko GitHub jest
    skonfigurowany — po wycięciu ścieżki zapisu to czysty odczyt, a odczyt jest domyślny.
    """
    from workmate.adapters.outbound.sqlite_events import SqliteEventStore
    from workmate.core.application.events import EventService
    from workmate.core.application.tools import build_activity_catalog

    events = EventService(SqliteEventStore(events_settings.db_path))

    if not (github_settings.token and github_settings.owner and github_settings.repo):
        return build_activity_catalog(events=events), None

    from workmate.adapters.outbound.sqlite_thread_links import SqliteThreadLinkStore
    from workmate.core.application.github import GithubWriteService

    # JEDEN klient GitHub na proces, współdzielony przez odczyt commitów i zapis — dwa klienty
    # do tego samego hosta trzymałyby dwie pule połączeń bez żadnego zysku.
    client = _github_client(github_settings)
    worklog = _worklog_service(client, github_settings)
    if not github_settings.enable_github_write:
        return build_activity_catalog(events=events, worklog=worklog), None

    write_service = GithubWriteService(
        client, owner=github_settings.owner, repo=github_settings.repo, events=events
    )
    thread_links = SqliteThreadLinkStore(events_settings.db_path)
    logger.info(
        "GitHub write WŁĄCZONY dla %s/%s — agent Teams może tworzyć issue/komentarze. "
        "Uwaga (ADR 0024): powiązanie wątku z issue trafi do nagłówka sesji TYLKO, gdy drzwi "
        "GitHub biegną z ENABLE_CHANNEL_THREADING=true na WSPÓLNYM events.db i tej samej parze "
        "team/channel — to notifier zapełnia mapę wątków. Bez tego mapa jest pusta, a agent "
        "komentuje wyłącznie na numer podany przez człowieka.",
        github_settings.owner,
        github_settings.repo,
    )
    return (
        build_activity_catalog(events=events, worklog=worklog, write_service=write_service),
        _make_thread_link_lookup(thread_links),
    )


def _github_client(github_settings: GithubSettings) -> HttpxGithubClient:
    """Zbuduj sync klienta GitHub żyjącego przez cały proces (daemon).

    Klient trzyma pulę połączeń, więc domykamy go przy wyjściu z procesu — bez tego pula
    zostaje sierotą i interpreter zamyka gniazda dopiero przy zbieraniu śmieci.
    """
    import atexit

    import httpx

    from workmate.adapters.outbound.github_api import HttpxGithubClient

    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    return HttpxGithubClient(transport, github_settings.token, api_base=github_settings.api_base)


def _worklog_service(client: GithubReadPort, github_settings: GithubSettings) -> WorklogService:
    """Serwis propozycji czasu z commitów (ADR 0034) — czysty ODCZYT, bez bramki.

    Bramki nie ma celowo: po wycięciu ścieżki zapisu nic tu nie mutuje, a repo trzyma zasadę
    „odczyt domyślny, bramkujemy zapis" (ADR 0006). Zdolność stoi wyłącznie na GitHubie — klucze
    Jira wyłuskujemy regexem z treści commitów, więc konfiguracja Jiry jest tu zbędna.

    Sufity estymacji egzekwujemy TU, bo ``GithubSettings.validate`` woła tylko poller GitHuba
    (``workmate-github``), a to te drzwi liczą propozycję (ta sama asymetria co przy Jirze).

    Zwraca SERWIS, nie katalog: od kroku 5.2 (ADR 0009) propozycja czasu jest akcją
    ``Activity(action='worklog')``, a nie własnym narzędziem.
    """
    from workmate.core.application.worklog import WorklogService
    from workmate.core.domain.worklog import SessionPolicy

    github_settings.validate_worklog_limits()
    service = WorklogService(
        client,
        owner=github_settings.owner,
        repo=github_settings.repo,
        policy=SessionPolicy(
            idle_gap_minutes=github_settings.worklog_idle_gap_minutes,
            ramp_up_minutes=github_settings.worklog_ramp_up_minutes,
            round_minutes=github_settings.worklog_round_minutes,
            max_session_hours=github_settings.worklog_max_session_hours,
            tz=ZoneInfo(github_settings.worklog_tz),
        ),
        max_range_days=github_settings.worklog_max_range_days,
    )
    logger.info(
        "Propozycja czasu z commitów WŁĄCZONA dla %s/%s (strefa %s) — narzędzie jest ODCZYTOWE, "
        "godziny do Jiry wprowadza człowiek arkuszem WorklogPRO (ADR 0035).",
        github_settings.owner,
        github_settings.repo,
        github_settings.worklog_tz,
    )
    return service


def _make_thread_link_lookup(
    thread_links: ThreadLinkStore,
) -> Callable[[str], tuple[str, int] | None]:
    """Odczyt powiązania wątek Teams ↔ issue/PR (ADR 0024, Faza 3b) — do NAGŁÓWKA SESJI.

    Z ``external_id`` (``team/channel/root`` — konwencja tych drzwi) czyta cel z zaufanego
    ``ThreadLinkStore``. Zwraca ``(rodzaj, numer)`` albo ``None``, gdy wątek nie jest z niczym
    powiązany. Numer pochodzi z mapowania, nie od modelu — to się nie zmienia.

    Do kroku 5.5 (ADR 0009 paczki) ta sama informacja jechała jako OSOBNE narzędzie
    ``reply_on_thread`` z numerem domkniętym w closurze. Narzędzie zniesiono, bo wołało tę samą
    metodę (``GithubWriteService.create_comment``) co ``Activity(action='comment')``, za tą samą
    bramką ``enable_github_write`` i obok niej w tym samym katalogu. Nie zawężało więc niczego:
    model, który chciałby skomentować inne issue, miał to drugie narzędzie pod ręką z numerem
    przyjmowanym wprost. Jedyną wartością było wypełnienie argumentu — czyli zastosowanie
    istniejącej zdolności, a takie rzeczy należą do treści promptu, nie do katalogu
    (kryterium §1 „audit harness primitives first").
    """

    def lookup(external_id: str) -> tuple[str, int] | None:
        parts = external_id.split("/")
        if len(parts) != 3:
            return None
        target = thread_links.get_target(*parts)
        if target is None:
            return None
        kind, number = target
        return kind, int(number)

    return lookup


def _build_file_reply_factory(
    settings: TeamsGraphSettings, token_provider: Callable[[], str]
) -> Callable[[str], list[ToolSpec]] | None:
    """Fabryka ``ReplyWithFile`` per turę (ADR 0026, A′2) — odpowiedź plikiem w wątku Teams.

    ``None``, gdy bramka ``enable_file_reply`` wyłączona (domyślnie, ADR 0006). Włączona: buduje
    SYNCHRONICZNY ``HttpxGraphFileSender`` (jak zapis GitHub Gate-4 — narzędzia agenta biegną
    synchronicznie w puli wątków) na TYM SAMYM delegowanym tokenie co poller, oraz renderer
    dokumentów. Cel dostawy (``team/channel/root``) wyłuskujemy z ``external_id`` wątku, NIE od
    modelu — plik ląduje wyłącznie w wątku bieżącej rozmowy (kontrola kompensująca, ADR 0026).
    """
    if not settings.enable_file_reply:
        return None
    try:
        import atexit

        import httpx

        from workmate.adapters.outbound.document_renderer import DefaultDocumentRenderer
        from workmate.adapters.outbound.graph_file_sender import HttpxGraphFileSender
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc
    from workmate.core.application.tools import build_file_reply_catalog

    # Sync klient żyje przez proces (daemon); pulę połączeń domykamy jawnie przy wyjściu.
    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    sender = HttpxGraphFileSender(transport, token_provider)
    renderer = DefaultDocumentRenderer()
    max_bytes = settings.max_file_reply_kb * 1024
    logger.info(
        "Odpowiedź plikiem WŁĄCZONA (ADR 0026) — agent Teams może załączać md/txt/pdf/docx w "
        "wątku. Wymaga zakresu 'Files.ReadWrite.All' na tokenie; limit pliku %d KB.",
        settings.max_file_reply_kb,
    )

    def factory(external_id: str) -> list[ToolSpec]:
        parts = external_id.split("/")
        if len(parts) != 3:
            return []
        team_id, channel_id, root_id = parts
        return build_file_reply_catalog(
            sender, renderer, team_id, channel_id, root_id, max_bytes=max_bytes
        )

    return factory


def _build_outbox_send_factory(
    settings: TeamsGraphSettings, token_provider: Callable[[], str]
) -> Callable[[str], Callable[[Deliverable], None] | None] | None:
    """Fabryka WYSYŁACZA skrzynki nadawczej rozmowy (ADR 0009 paczki) — plik z ``outputs/`` w wątek.

    ``None``, gdy bramka ``enable_file_reply`` wyłączona: to ta sama zdolność co ``ReplyWithFile``
    (ADR 0026) — załącznik w wątku Teams — więc dzieli z nią bramkę i limit rozmiaru. Osobna byłaby
    obietnicą, że operator włączył jedno, a dostał dwa.

    Cel dostawy (``team/channel/root``) wyłuskujemy z ``external_id`` wątku, NIE od modelu — plik
    trafia wyłącznie do wątku bieżącej rozmowy (kontrola kompensująca, ADR 0026 §Threat model).
    Wątek o innym kształcie ``external_id`` daje ``None``: nie ma dokąd wysłać, więc skrzynka
    zostaje nietknięta zamiast zostać opróżniona w próżnię.
    """
    if not settings.enable_file_reply:
        return None
    try:
        import atexit

        import httpx

        from workmate.adapters.outbound.graph_file_sender import HttpxGraphFileSender
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc
    from workmate.core.errors import ThreadRootGone
    from workmate.core.ports.outbox import PermanentDeliveryError

    # Timeout ROZPISANY na fazy, nie skalarem: `httpx` rozdziela go i tak na connect/read/write,
    # a skalar 30 s znaczy tu 30 s NA KAŻDĄ z nich. Zapis 512 KB (sufit `max_file_reply_kb`)
    # potrzebuje więcej niż połączenie, więc `write` jest hojniejszy niż `connect`.
    transport = httpx.Client(timeout=httpx.Timeout(connect=5, read=15, write=15, pool=5))
    atexit.register(transport.close)
    # Bez ponawiania 5xx/timeoutów — skrzynka MA WŁASNĄ pętlę ponowień (plik zostaje, następna
    # tura próbuje, licznik prób kończy po trzeciej). Druga warstwa retry mnożyłaby najgorszy
    # przypadek czterokrotnie, a to opóźnienie płaci rozmówca czekający na odpowiedź tury.
    sender = HttpxGraphFileSender(transport, token_provider, retry_transient=False)

    def factory(external_id: str) -> Callable[[Deliverable], None] | None:
        parts = external_id.split("/")
        if len(parts) != 3:
            return None
        team_id, channel_id, root_id = parts

        def send(item: Deliverable) -> None:
            try:
                uploaded = sender.upload_channel_file(
                    team_id, channel_id, item.name, item.content, item.content_type
                )
                sender.post_reply_with_attachment(
                    team_id, channel_id, root_id, _outbox_html(uploaded.name), uploaded
                )
            except ThreadRootGone as exc:
                raise PermanentDeliveryError("wątek tej rozmowy już nie istnieje") from exc
            except httpx.HTTPStatusError as exc:
                # 4xx (poza 429) znaczy „Graph tego pliku nie przyjmie" — nazwa odrzucona przez
                # SharePoint, kanał bez folderu plików, brak zakresu. Ponowienie da to samo,
                # a plik zostawiony w skrzynce doklejałby „spróbuję ponownie" do KAŻDEJ kolejnej
                # odpowiedzi w tej rozmowie, płacąc przy tym dwa żądania Graph za turę.
                status = exc.response.status_code
                if 400 <= status < 500 and status != 429:
                    raise PermanentDeliveryError(f"Graph odrzucił plik (HTTP {status})") from exc
                raise
            except RuntimeError as exc:
                # `graph_file_sender._require` podnosi `RuntimeError` na odpowiedzi 200
                # z NIEPEŁNYM payloadem — kanał bez `filesFolder.driveId` (brak dysku plików)
                # albo `eTag` bez GUID-a. To własność KANAŁU i odpowiedzi, nie chwili: ponowienie
                # da to samo. Bez tej gałęzi plik zostawałby w skrzynce na zawsze, a każda tura
                # płaciłaby dwa żądania i doklejała „spróbuję ponownie" — czyli dokładnie zatrutą
                # wiadomość, której reguła sprzątania ma unikać.
                raise PermanentDeliveryError(f"kanał nie przyjmuje plików ({exc})") from exc

        return send

    return factory


def _outbox_html(filename: str) -> str:
    """Zaufany, ESCAPOWANY HTML wiadomości niosącej załącznik ze skrzynki (składany u nas)."""
    return f"<p>{escape(filename)}</p>"


def _build_user_push_factory(
    settings: TeamsGraphSettings, token_provider: Callable[[], str]
) -> Callable[[str], list[ToolSpec]] | None:
    """Fabryka ``SendImage`` per turę (ADR 0027, A′3) — push obrazu do rozmówcy 1:1.

    ``None``, gdy bramka ``enable_user_file_push`` wyłączona (domyślnie, ADR 0006). Włączona: buduje
    SYNCHRONICZNY ``HttpxGraphUserImagePush`` (jak plik ADR 0026 — narzędzia agenta biegną
    synchronicznie w puli wątków) na TYM SAMYM delegowanym tokenie co poller. Cel (odbiorca) NIE
    pochodzi z ``external_id`` wątku, lecz z ``sender_id`` bieżącej wiadomości — dlatego to OSOBNA
    fabryka (klucz = nadawca), nie składana z fabrykami wątkowymi. Obraz idzie INLINE
    (hostedContents), bez dysku SharePoint, więc bez zakresu ``Files.*`` — ale 1:1 wymaga czatu.
    """
    if not settings.enable_user_file_push:
        return None
    try:
        import atexit

        import httpx

        from workmate.adapters.outbound.graph_user_push import HttpxGraphUserImagePush
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc
    from workmate.core.application.tools import build_user_image_push_catalog

    # Sync klient żyje przez proces (daemon); pulę połączeń domykamy jawnie przy wyjściu.
    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    sender = HttpxGraphUserImagePush(transport, token_provider)
    max_bytes = settings.max_user_image_kb * 1024
    logger.info(
        "Push obrazu do usera WŁĄCZONY (ADR 0027) — agent Teams może odesłać obraz rozmówcy 1:1. "
        "Wymaga zakresów czatu (Chat.Create/ChatMessage.Send) na tokenie; limit obrazu %d KB.",
        settings.max_user_image_kb,
    )

    def factory(sender_id: str) -> list[ToolSpec]:
        if not sender_id:
            return []
        return build_user_image_push_catalog(sender, sender_id, max_bytes=max_bytes)

    return factory


def _build_user_doc_push_factory(
    settings: TeamsGraphSettings, token_provider: Callable[[], str]
) -> Callable[[str], list[ToolSpec]] | None:
    """Fabryka ``SendDocument`` per turę (ADR 0027, wariant plikowy) — push pliku 1:1.

    ``None``, gdy bramka ``enable_user_doc_push`` wyłączona (domyślnie, ADR 0006). Włączona: buduje
    SYNCHRONICZNY ``HttpxGraphUserDocPush`` (jak plik ADR 0026 — narzędzia agenta biegną
    synchronicznie w puli wątków) na TYM SAMYM delegowanym tokenie co poller, oraz renderer
    dokumentów. Cel (odbiorca) pochodzi z ``sender_id`` bieżącej wiadomości (jak wariant obrazowy),
    NIE od modelu. W odróżnieniu od obrazu plik ląduje na OneDrive bota → wymaga zakresu
    ``Files.ReadWrite.All`` obok zakresów czatu (walidacja fail-fast w ``config``).
    """
    if not settings.enable_user_doc_push:
        return None
    try:
        import atexit

        import httpx

        from workmate.adapters.outbound.document_renderer import DefaultDocumentRenderer
        from workmate.adapters.outbound.graph_user_doc_push import HttpxGraphUserDocPush
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc
    from workmate.core.application.tools import build_user_doc_push_catalog

    # Sync klient żyje przez proces (daemon); pulę połączeń domykamy jawnie przy wyjściu.
    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    sender = HttpxGraphUserDocPush(transport, token_provider)
    renderer = DefaultDocumentRenderer()
    max_bytes = settings.max_user_doc_kb * 1024
    logger.info(
        "Push dokumentu do usera WŁĄCZONY (ADR 0027) — agent Teams może odesłać plik (md/txt/pdf/"
        "docx) rozmówcy 1:1. Wymaga zakresów czatu ORAZ 'Files.ReadWrite.All' na tokenie; limit "
        "pliku %d KB.",
        settings.max_user_doc_kb,
    )

    def factory(sender_id: str) -> list[ToolSpec]:
        if not sender_id:
            return []
        return build_user_doc_push_catalog(sender, renderer, sender_id, max_bytes=max_bytes)

    return factory


def _build_team_schedule_catalog(schedule_settings: ScheduleSettings) -> list[ToolSpec]:
    """Zbuduj katalog grafiku Shifts (ADR 0059) — pusty, gdy grafik wyłączony/nieskonfigurowany.

    ``is_enabled()`` (tryb auto) sam sprawdza obecność cudzego cache MSAL, więc na hoście bez montu
    powiadomienia-teams po prostu nie dokładamy narzędzia (ciche wyłączenie). Klient żyje przez cały
    proces (daemon), jak inne sync klienty tutaj.
    """
    if not schedule_settings.is_enabled():
        logger.info(
            "Grafik Shifts WYŁĄCZONY (ADR 0059) — brak cache tokenu %s albo "
            "WORKMATE_SCHEDULE_ENABLED=false. Narzędzie `Schedule` nie zostanie wystawione.",
            schedule_settings.token_cache_path,
        )
        return []
    import atexit

    import httpx

    from workmate.adapters.outbound.graph_schedule_api import HttpxGraphScheduleClient
    from workmate.adapters.outbound.msal_silent_token import build_silent_token_provider
    from workmate.core.application.team_schedule import TeamScheduleService
    from workmate.core.application.tools import build_schedule_catalog

    token_provider = build_silent_token_provider(schedule_settings)
    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    client = HttpxGraphScheduleClient(transport, token_provider)
    service = TeamScheduleService(
        client, team_id=schedule_settings.team_id, tz=schedule_settings.timezone
    )
    logger.info(
        "Grafik Shifts WŁĄCZONY (ADR 0059) — agent Teams pokazuje zmiany i nieobecności zespołu "
        "%s (strefa %s), token cichy z cache %s (RO, nigdy nie zapisywany).",
        schedule_settings.team_id,
        schedule_settings.timezone,
        schedule_settings.token_cache_path,
    )
    return build_schedule_catalog(service)


def _build_my_jira_tasks_factory(
    settings: TeamsGraphSettings, jira_settings: JiraSettings
) -> Callable[[str], list[ToolSpec]] | None:
    """Fabryka "moje zadania" Jira (ADR 0054) PER NADAWCA — ``None`` gdy nieskonfigurowana.

    Wymaga skonfigurowanego odczytu Jiry (URL+token) ORAZ mapy tożsamości — TEGO SAMEGO pliku co
    autoryzacja M3 (ADR 0042, pole ``jira_user``), niezależnie od bramki zapisu notatek. Sender bez
    rozwiązanej tożsamości albo bez ``jira_user`` dostaje pustą listę narzędzi (fail-closed, zero
    domysłów) — router komend i responder degradują to do czytelnej odmowy, nie do błędu.

    Zawężenie do WŁASNEGO konta dzieje się TU, przy budowie ``MyJiraTasksService``, i to jest
    jedyne miejsce, gdzie ono żyje (ADR 0054). Od kroku 5.3 (ADR 0009 paczki wdrożeniowej) sześć
    dawnych narzędzi jest jednym ``Jira(action=…)``, więc pole ``member`` STOI w tym samym
    schemacie co akcje ``my_*`` — ale gałęzie ``my_*`` go nie czytają, bo biorą serwis domknięty
    tutaj. Sonda pilnująca tego jest w ``tests/core/test_jira_catalog.py``; dawniej niemożliwość
    przekierowania wynikała z pustej sygnatury, teraz wynika z dispatchera i musi być sprawdzana.
    """
    if not (jira_settings.base_url and jira_settings.token):
        return None
    if not settings.meeting_note_identities.is_file():
        return None
    import atexit

    import httpx

    from workmate.adapters.outbound.graph_identity_directory import YamlIdentityDirectory
    from workmate.adapters.outbound.jira_api import build_jira_client
    from workmate.core.application.jira_read import JiraReadService
    from workmate.core.application.my_jira_tasks import MyJiraTasksService
    from workmate.core.application.tools import build_jira_catalog

    identities = YamlIdentityDirectory(settings.meeting_note_identities)
    # Klient żyje przez cały proces (daemon), jak inne sync klienty Jiry/GitHuba tutaj.
    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    client = build_jira_client(transport, jira_settings)
    # Serwis rozszerzonego odczytu (szczegóły/wyszukiwanie/członek) współdzieli klienta i bazę URL —
    # NIE jest związany z nadawcą (bierze parametry), więc budujemy go raz.
    read_service = JiraReadService(client, base_url=jira_settings.base_url)

    def resolve_member(name: str) -> str | None:
        person = identities.resolve_by_display_name(name)
        return person.jira_user if person and person.jira_user else None

    logger.info(
        "'Moje zadania' Jira WŁĄCZONE (ADR 0054) — agent Teams i komenda /moje-zadania pokazują "
        "otwarte zadania nadawcy, zawężone do JEGO konta Jira przez mapę tożsamości %s. "
        "Dodatkowo rozszerzony ODCZYT (szczegóły zgłoszenia, wyszukiwanie, zadania członka).",
        settings.meeting_note_identities,
    )

    def factory(sender_id: str) -> list[ToolSpec]:
        if not sender_id:
            return []
        person = identities.resolve_by_aad_user_id(sender_id)
        if person is None or not person.jira_user:
            return []
        service = MyJiraTasksService(
            client, assignee=person.jira_user, base_url=jira_settings.base_url
        )
        return build_jira_catalog(service, read_service, resolve_member)

    return factory


def _build_note_read_authorizer(settings: TeamsGraphSettings) -> NoteReadAuthorizer | None:
    """Bramka członkostwa ODCZYTU bazy wiedzy (ADR 0062) albo ``None``.

    ``None``, gdy ``enable_note_read_authz`` wyłączona (domyślnie) — odczyt zachowuje się jak przed
    ADR 0062. Włączona: config wymusił istnienie mapy tożsamości (ten sam plik co zapis, ADR
    0042/0062), więc składamy authorizer nad ``YamlIdentityDirectory`` (fail-closed). Wpinany w
    ``_build_responder``: bramkuje per-turowe narzędzia odczytu agenta (``SearchNotes``/``GetNote``/
    ``ListProjects``) ORAZ komendy ``/szukaj``/``/projekty``. Powłoka i drzwi MCP są POZA zakresem —
    nie niosą tożsamości nadawcy (ADR 0062 §Decision 4 i addendum).
    """
    if not settings.enable_note_read_authz:
        return None
    from workmate.adapters.outbound.graph_identity_directory import YamlIdentityDirectory
    from workmate.core.application.note_read_authz import NoteReadAuthorizer

    logger.info(
        "Autoryzacja ODCZYTU bazy wiedzy WŁĄCZONA (ADR 0062) — narzędzia agenta oraz komendy "
        "/szukaj i /projekty wymagają rozpoznanego członka pionu przez mapę tożsamości %s "
        "(fail-closed). Powłoka (cat/workmate-search po montażu ro) i drzwi MCP są POZA zakresem.",
        settings.meeting_note_identities,
    )
    return NoteReadAuthorizer(YamlIdentityDirectory(settings.meeting_note_identities))


def _build_shell_authorizer(
    settings: TeamsGraphSettings, shell_settings: ShellSettings
) -> ShellAuthorizer | None:
    """Bramka członkostwa POWŁOKI (ADR 0063) albo ``None``.

    ``None``, gdy powłoka wyłączona (``WORKMATE_ENABLE_SHELL`` domyślnie OFF) — powłoki wtedy nie
    ma, nie ma czego bramkować. Włączona: WYMAGA mapy tożsamości (fail-fast tutaj, jak zapis ADR
    0042 / odczyt ADR 0062), bo bez niej bramka nie miałaby po czym rozpoznać nadawcy — i wtedy
    każdy dostałby powłokę, czyli dokładnie luka, którą ADR 0063 zamyka. Bramka jest WBUDOWANA we
    flagę powłoki (nie osobny toggle): powłoka jest już opt-in OFF, więc „shell ON" znaczy „ON i
    bramkowany", bez okna otwartego. Składa authorizer nad ``YamlIdentityDirectory`` (fail-closed).
    Wpinany w ``_build_responder``; egzekwuje per-turową fabrykę powłoki po nadawcy. Drzwi MCP i CLI
    (jeden zaufany operator, brak ``sender_id``) są POZA zakresem, jak w ADR 0042/0062.
    """
    if not shell_settings.enabled:
        return None
    if not settings.meeting_note_identities.is_file():
        # Powłoka bramkowana członkostwem (ADR 0063): bez mapy tożsamości nie ma po czym rozpoznać
        # nadawcy, więc bramka nie miałaby jak działać — a powłoka sięga ścieżką bezwzględną poza
        # scope rozmowy. Fail-fast (ten sam plik co zapis/odczyt i worklogi).
        raise SystemExit(
            "WORKMATE_ENABLE_SHELL=true na drzwiach Teams wymaga WORKMATE_TEAMS_GRAPH_IDENTITIES "
            "= ścieżka do mapy tożsamości (członkostwo bramkuje powłokę, ADR 0063); brak pliku: "
            f"{settings.meeting_note_identities}."
        )
    from workmate.adapters.outbound.graph_identity_directory import YamlIdentityDirectory
    from workmate.core.application.shell_authz import ShellAuthorizer

    logger.info(
        "Bramka członkostwa POWŁOKI WŁĄCZONA (ADR 0063) — narzędzie Bash tylko dla rozpoznanego "
        "członka pionu przez mapę tożsamości %s (fail-closed). Cross-read MIĘDZY członkami domyka "
        "montaż per-rozmowa: wykonawca stoi na każdą rozmowę osobno i widzi wyłącznie jej "
        "podkatalog brudnopisu (infra ADR 0012).",
        settings.meeting_note_identities,
    )
    return ShellAuthorizer(YamlIdentityDirectory(settings.meeting_note_identities))


def _build_meeting_note_router(
    settings: TeamsGraphSettings,
    token_provider: Callable[[], str],
    core_settings: Settings,
    agent_settings: AgentSettings,
) -> MeetingNoteRouter | None:
    """Router komendy ZAPISU ``/notatka`` (produkcyjne M3, ADR 0009 §4 / 0041) albo ``None``.

    ``None``, gdy bramka ``enable_meeting_note_write`` wyłączona (domyślnie, ADR 0006). Włączona:
    składa przepływ M3 z realnych adapterów — transkrypt z Graph (``HttpxGraphTranscriptSource`` na
    tym samym delegowanym tokenie co poller), streszczenie przez Claude (adapter summarizera, extra
    ``agent``) i ZAPIS przez bramkowany, DOPISUJĄCY ``NotesWriteService`` (create-only, ADR
    0006) do PRAWDZIWEJ bazy ``data/notes/``. Config waliduje, że transkrypt jest włączony (skąd
    wziąć treść). ``project``/``date``/``ref`` bierze router z argumentów komendy, nie z treści.
    """
    if not settings.enable_meeting_note_write:
        return None
    try:
        from workmate.adapters.outbound.anthropic_summarizer import AnthropicMeetingSummarizer
        from workmate.adapters.outbound.transcript_sources import HttpxGraphTranscriptSource
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc
    from workmate.adapters.inbound.meeting_command import MeetingNoteRouter
    from workmate.adapters.outbound.graph_identity_directory import YamlIdentityDirectory
    from workmate.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
    from workmate.adapters.outbound.yaml_projects_repo import YamlProjectsRepository
    from workmate.core.application.meeting_authz import MeetingNoteAuthorizer
    from workmate.core.application.meeting_notes import MeetingNoteService
    from workmate.core.application.services import NotesWriteService

    transcripts = HttpxGraphTranscriptSource(token_provider)
    summarizer = AnthropicMeetingSummarizer(agent_settings)
    # Pass 2 (ADR 0047): ten sam adapter jest też krytykiem. Włączany flagą jakości (OFF domyślnie,
    # ~2× koszt) — NIE bramka zapisu. None → jednoprzelotowo (0041).
    verifier = summarizer if agent_settings.verify_meeting_note else None
    write_service = NotesWriteService(
        MarkdownNotesWriter(core_settings.notes_dir),
        YamlProjectsRepository(core_settings.projects_registry),
    )
    # Autoryzacja nadawcy (B2 / ADR 0042): AAD id → członek pionu przez katalog tożsamości
    # (fail-closed; config wymusił istnienie pliku). Ta sama mapa zasila "moje zadania" Jira
    # (ADR 0054, pole jira_user) — patrz _build_my_jira_tasks_factory.
    authorizer = MeetingNoteAuthorizer(YamlIdentityDirectory(settings.meeting_note_identities))
    # Async (B3 / ADR 0043): przy włączonej bramce async router dostaje scheduler (pula wątków) i
    # callback (sync poster do wątku); inaczej ``(None, None)`` → router liczy inline (0041).
    scheduler, callback = _build_async_note_dispatch(settings, token_provider)
    logger.info(
        "Komenda /notatka WŁĄCZONA (ADR 0009/0041) — agent Teams może złożyć notatkę ze spotkania "
        "z transkryptu Graph do data/notes/ (zapis create-only, ADR 0006). Autoryzacja nadawcy "
        "przez mapę tożsamości %s (członkostwo, ADR 0042). Tryb: %s. Wymaga zakresów transkryptu "
        "na tokenie oraz extra 'agent' (Claude).",
        settings.meeting_note_identities,
        "ASYNC (ack + tło + callback, ADR 0043)" if scheduler else "synchroniczny (inline)",
    )
    return MeetingNoteRouter(
        MeetingNoteService(transcripts, summarizer, write_service, verifier=verifier),
        authorizer=authorizer,
        scheduler=scheduler,
        callback=callback,
    )


def _build_thread_note_router(
    settings: TeamsGraphSettings,
    token_provider: Callable[[], str],
    core_settings: Settings,
    agent_settings: AgentSettings,
) -> ThreadNoteRouter | None:
    """Router przechwycenia „zapisz to" z wątku (ADR 0048, F2) albo ``None``.

    ``None``, gdy bramka ``enable_thread_note_capture`` wyłączona (domyślnie, ADR 0006). Włączona:
    składa przepływ z realnych adapterów — treść wątku z Graph (``HttpxGraphThreadSource`` na tym
    samym delegowanym tokenie co poller, SYNC), streszczenie przez Claude (reuse summarizera M3) i
    ZAPIS przez create-only ``NotesWriteService`` do ``data/notes/``. Autoryzacja nadawcy (B2 /
    ADR 0042) reużywa mapy tożsamości (config wymusił plik). Async współdzieli pulę/poster
    ``/notatka`` (``_build_async_note_dispatch``). ``project`` bierze router z argumentu wzmianki,
    nie z treści wątku (ADR 0009 §3).
    """
    if not settings.enable_thread_note_capture:
        return None
    import atexit

    try:
        import httpx

        from workmate.adapters.outbound.anthropic_summarizer import AnthropicMeetingSummarizer
        from workmate.adapters.outbound.graph_thread_source import HttpxGraphThreadSource
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc
    from workmate.adapters.inbound.thread_note_command import ThreadNoteRouter
    from workmate.adapters.outbound.graph_identity_directory import YamlIdentityDirectory
    from workmate.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
    from workmate.adapters.outbound.yaml_projects_repo import YamlProjectsRepository
    from workmate.core.application.meeting_authz import MeetingNoteAuthorizer
    from workmate.core.application.services import NotesWriteService
    from workmate.core.application.thread_notes import ThreadNoteService

    # Sync klient httpx żyje przez proces (jak poster async_dispatch); domykamy przy wyjściu.
    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    source = HttpxGraphThreadSource(transport, token_provider)
    summarizer = AnthropicMeetingSummarizer(agent_settings)
    verifier = summarizer if agent_settings.verify_meeting_note else None
    write_service = NotesWriteService(
        MarkdownNotesWriter(core_settings.notes_dir),
        YamlProjectsRepository(core_settings.projects_registry),
    )
    authorizer = MeetingNoteAuthorizer(YamlIdentityDirectory(settings.meeting_note_identities))
    scheduler, callback = _build_async_note_dispatch(settings, token_provider)
    logger.info(
        "Przechwycenie 'zapisz to' WŁĄCZONE (ADR 0048) — @wzmianka bota z dyrektywą zapisuje wątek "
        "kanału jako notatkę do data/notes/ (create-only, ADR 0006). Autoryzacja nadawcy przez "
        "mapę tożsamości %s (członkostwo, ADR 0042). Tryb: %s.",
        settings.meeting_note_identities,
        "ASYNC (ack + tło + callback, ADR 0043)" if scheduler else "synchroniczny (inline)",
    )
    return ThreadNoteRouter(
        ThreadNoteService(source, summarizer, write_service, verifier=verifier),
        authorizer=authorizer,
        scheduler=scheduler,
        callback=callback,
    )


def _events_service(events_settings: EventsSettings) -> EventService | None:
    """``EventService`` nad wspólnym ``events.db`` TYLKO gdy plik istnieje (most zdarzeń w użyciu).

    Wspólny helper dla briefu (aktywność w statusie, ADR 0029) i digestu (fold zdarzeń, ADR 0052).
    Bez pliku ``None`` — drzwi nie tworzą pustego ``events.db`` tylko pod odczyt.
    """
    from pathlib import Path

    from workmate.adapters.outbound.sqlite_events import SqliteEventStore
    from workmate.core.application.events import EventService

    if not Path(str(events_settings.db_path)).expanduser().exists():
        return None
    return EventService(SqliteEventStore(events_settings.db_path))


def _build_brief_router(
    settings: TeamsGraphSettings,
    core_settings: Settings,
    events_settings: EventsSettings,
    deliver_pdf: Callable[[str, str, str], None] | None,
    read_authorizer: NoteReadAuthorizer | None = None,
) -> BriefRouter | None:
    """Router one-pagera „ogarnij mnie na <projekt>" (ADR 0051, F4) albo ``None``.

    ``None``, gdy bramka ``enable_project_brief`` wyłączona (domyślnie). Włączona: składa READ-ONLY
    ``ProjectBriefService`` nad tymi samymi repo notatek/projektów co runtime — status (pełna
    synteza, aktywność GitHub gdy most zdarzeń istnieje) + ostatnie notatki. Bez zapisu i bez
    nowego narzędzia MCP (golden surface nietknięty), ale Z bramką członkostwa ODCZYTU
    (``read_authorizer``, ADR 0062): brief serwuje treść notatek, więc „read-only" go z niej nie
    zwalnia. ``deliver_pdf`` (współdzielony, ADR 0026) wysyła ``| pdf`` plikiem; ``None`` →
    ``| pdf`` degraduje do tekstu.
    """
    if not settings.enable_project_brief:
        return None
    from workmate.adapters.inbound.brief_command import BriefRouter
    from workmate.adapters.outbound.markdown_notes_repo import MarkdownNotesRepository
    from workmate.adapters.outbound.yaml_projects_repo import YamlProjectsRepository
    from workmate.core.application.project_brief import ProjectBriefService
    from workmate.core.application.services import NotesService, ProjectsService

    notes_repo = MarkdownNotesRepository(core_settings.notes_dir)
    projects_repo = YamlProjectsRepository(core_settings.projects_registry)
    # Empty-query search zwraca notatki po dacie (nie rankinguje zapytania), więc NotesService bez
    # extra retrievalu — brief listuje NAJŚWIEŻSZE notatki projektu.
    service = ProjectBriefService(
        NotesService(notes_repo),
        ProjectsService(projects_repo, notes_repo, events=_events_service(events_settings)),
    )
    logger.info(
        "One-pager 'ogarnij mnie na <projekt>' WŁĄCZONY (ADR 0051) — @wzmianka bota zwraca brief "
        "projektu (status + ostatnie notatki), READ-ONLY. Dostawa PDF: %s.",
        "włączona (reuse file-reply)" if deliver_pdf else "wyłączona (| pdf → tekst)",
    )
    return BriefRouter(service, deliver_pdf=deliver_pdf, read_authorizer=read_authorizer)


def _build_change_digest_router(
    settings: TeamsGraphSettings,
    events_settings: EventsSettings,
    deliver_pdf: Callable[[str, str, str], None] | None,
    read_authorizer: NoteReadAuthorizer | None = None,
) -> ChangeDigestRouter | None:
    """Router digestu „co się zmieniło od <data>" (ADR 0052, F5) albo ``None``.

    ``None``, gdy bramka ``enable_change_digest`` wyłączona (domyślnie). Włączona: składa READ-ONLY
    ``ChangeDigestService`` nad wspólnym ``events.db`` (fold zdarzeń od daty, per projekt). Bez
    mostu zdarzeń → digest pusty (dozwolona degradacja). Bez zapisu/autoryzacji/nowego narzędzia.
    ``deliver_pdf`` (współdzielony z briefem) wysyła ``| pdf`` plikiem; inaczej degraduje do tekstu.
    """
    if not settings.enable_change_digest:
        return None
    from workmate.adapters.inbound.change_command import ChangeDigestRouter
    from workmate.core.application.change_digest import ChangeDigestService

    service = ChangeDigestService(_events_service(events_settings))
    logger.info(
        "Digest 'co się zmieniło od <data>' WŁĄCZONY (ADR 0052) — @wzmianka bota zwraca przegląd "
        "zmian od daty (fold zdarzeń per projekt), READ-ONLY. Dostawa PDF: %s.",
        "włączona (reuse file-reply)" if deliver_pdf else "wyłączona (| pdf → tekst)",
    )
    return ChangeDigestRouter(service, deliver_pdf=deliver_pdf, read_authorizer=read_authorizer)


def _build_thread_pdf_delivery(
    settings: TeamsGraphSettings, token_provider: Callable[[], str]
) -> Callable[[str, str, str], None] | None:
    """Współdzielone zamknięcie dostawy PLIKIEM PDF w wątku (ADR 0026) — brief (F4) i digest (F5).

    Reużywa kanał file-reply: renderer dokumentów + ``HttpxGraphFileSender`` na TYM SAMYM
    delegowanym tokenie co poller. Aktywne TYLKO przy włączonej bramce ``enable_file_reply`` (zakres
    ``Files.ReadWrite.All`` i sender) — inaczej ``None`` i ``| pdf`` degraduje do tekstu. Cel
    (``team/channel/root``) wyłuskujemy z ZAUFANEGO ``external_id`` wątku, NIE od modelu.
    """
    if not settings.enable_file_reply:
        return None
    try:
        import atexit

        import httpx

        from workmate.adapters.outbound.document_renderer import DefaultDocumentRenderer
        from workmate.adapters.outbound.graph_file_sender import HttpxGraphFileSender
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc
    from workmate.core.application.tools import build_file_reply_catalog

    # Sync klient żyje przez proces (jak inne sync sendery tu); pulę domykamy przy wyjściu.
    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    sender = HttpxGraphFileSender(transport, token_provider)
    renderer = DefaultDocumentRenderer()
    max_bytes = settings.max_file_reply_kb * 1024

    def deliver(external_id: str, base_name: str, content: str) -> None:
        parts = external_id.split("/")
        if len(parts) != 3:
            raise ValueError(f"zły external_id wątku (team/channel/root): {external_id!r}")
        team_id, channel_id, root_id = parts
        # Reuse JEDNOŹRÓDŁOWEGO pipeline'u file-reply (ADR 0026, reguła 6): render → walidacja →
        # ``_safe_doc_name`` (hardening nazwy) → upload → post. Bez duplikacji sekwencji tutaj.
        (spec,) = build_file_reply_catalog(
            sender, renderer, team_id, channel_id, root_id, max_bytes=max_bytes
        )
        result = spec.fn(content, "pdf", base_name)
        if "error" in result:
            # Błąd oczekiwany (zły format/za duży/ThreadRootGone) → wywal, router zdegraduje do
            # tekstu. Twarde awarie infrastruktury pipeline PUSZCZA wyżej (SafeResponder je złapie).
            raise RuntimeError(str(result["error"]))

    return deliver


def _build_async_note_dispatch(
    settings: TeamsGraphSettings, token_provider: Callable[[], str]
) -> tuple[Callable[[Callable[[], None]], None] | None, Callable[[str, str], None] | None]:
    """Scheduler (pula wątków) + callback (sync poster do wątku) dla async ``/notatka`` (ADR 0043).

    ``(None, None)``, gdy ``enable_meeting_note_async`` wyłączona — router liczy inline (0041).
    Włączona: OGRANICZONA pula wątków (``meeting_note_async_workers`` = sufit równoległych łańcuchów
    transkrypt+Claude) i ``HttpxGraphThreadReplyPoster`` (sync, ten sam delegowany token co poller).
    Callback wyłuskuje cel ``team/channel/root`` z ``external_id`` wątku (NIE od modelu) i tam
    wrzuca wynik. Pula i klient żyją przez proces; domykamy je przy wyjściu (jak inne sync klienty).
    """
    if not settings.enable_meeting_note_async:
        return None, None
    import atexit
    from concurrent.futures import ThreadPoolExecutor

    try:
        import httpx

        from workmate.adapters.outbound.graph_thread_reply import HttpxGraphThreadReplyPoster
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc

    executor = ThreadPoolExecutor(
        max_workers=settings.meeting_note_async_workers, thread_name_prefix="meeting-note"
    )
    atexit.register(lambda: executor.shutdown(wait=False))
    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    poster = HttpxGraphThreadReplyPoster(transport, token_provider)

    def scheduler(thunk: Callable[[], None]) -> None:
        # Zlecenie do puli jest NIEBLOKUJĄCE; Future świadomie porzucamy (wynik idzie do wątku,
        # nie do wołającego). Przekroczenie puli → zadania czekają w kolejce (bounded równoległość).
        executor.submit(thunk)

    def callback(external_id: str, text: str) -> None:
        parts = external_id.split("/")
        if len(parts) != 3:
            logger.warning("Zły external_id callbacku /notatka: %r — pomijam.", external_id)
            return
        team_id, channel_id, root_id = parts
        poster.post(team_id, channel_id, root_id, text)

    logger.info(
        "Async /notatka WŁĄCZONY (ADR 0043) — ACK natychmiast, łańcuch w tle (%d wątków), wynik do "
        "wątku kanału. Idempotencja (deterministyczny id) chroni retry.",
        settings.meeting_note_async_workers,
    )
    return scheduler, callback


def _compose_user_push_factories(
    *factories: Callable[[str], list[ToolSpec]] | None,
) -> Callable[[str], list[ToolSpec]] | None:
    """Złóż fabryki push-u 1:1 (obraz + dokument) w jedną; ``None`` gdy żadna bramka nie jest ON.

    Obie kluczowane ``sender_id`` (odbiorca = nadawca bieżącej wiadomości, ADR 0027) i niezależnie
    bramkowane, a responder przyjmuje JEDNĄ ``user_push_tool_factory`` — łączymy je konkatenacją
    wyników, by obie zdolności współistniały bez zmiany kontraktu respondera (jak
    ``_compose_thread_factories`` dla narzędzi wątkowych).
    """
    active = [f for f in factories if f is not None]
    if not active:
        return None
    if len(active) == 1:
        return active[0]

    def combined(sender_id: str) -> list[ToolSpec]:
        tools: list[ToolSpec] = []
        for factory in active:
            tools.extend(factory(sender_id))
        return tools

    return combined


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


def _build_mutation_identities(settings: TeamsGraphSettings) -> object | None:
    """Mapa tożsamości dla MUTACJI bazy wiedzy (ADR 0065) albo ``None`` — fail-closed.

    Osobno od ``_build_note_read_authorizer``, bo to inna bramka i inny plik konfiguracji mógłby
    ją włączyć. Wspólny jest za to warunek konieczny: bez mapy nie ma komu przypisać zmiany
    ani kogo zapytać o potwierdzenie, więc brak pliku ZAMYKA mutacje zamiast je przepuścić.
    """
    if not settings.enable_note_mutation:
        return None
    if not settings.meeting_note_identities.is_file():
        logger.error(
            "WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_MUTATION=true, ale mapy tożsamości %s nie ma — "
            "mutacje bazy wiedzy POZOSTAJĄ WYŁĄCZONE (fail-closed, ADR 0065).",
            settings.meeting_note_identities,
        )
        return None
    from workmate.adapters.outbound.graph_identity_directory import YamlIdentityDirectory

    logger.warning(
        "MUTACJA bazy wiedzy WŁĄCZONA (ADR 0065): agent może zmieniać notatki przez File(edit)"
        "%s. Każda zmiana idzie przez migawkę i niezależnego sędziego; nadawca musi być "
        "rozpoznany przez mapę %s.",
        " ORAZ JE USUWAĆ (File(delete))" if settings.enable_note_delete else "",
        settings.meeting_note_identities,
    )
    return YamlIdentityDirectory(settings.meeting_note_identities)
