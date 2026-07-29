"""Entry point drzwi Teams w trybie DELEGOWANYM (ADR 0015) — polling kanału przez Graph.

Bot odpowiada RUNTIME AGENTA rdzenia (jak drzwi bota/Telegram), ale działa jako
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
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from workmate.adapters.inbound import env
from workmate.adapters.inbound.agent_wiring import build_conversational_responder
from workmate.adapters.inbound.teams_graph.handler import make_handle_message
from workmate.adapters.outbound.filesystem_workspace import prune_stale
from workmate.config import (
    JIRA_DEPLOYMENTS,
    MAX_JIRA_TRANSITION_HOPS,
    AgentSettings,
    ConversationSettings,
    EventsSettings,
    GithubSettings,
    JiraSettings,
    Settings,
    TeamsGraphSettings,
    WorkspaceSettings,
    require_writable,
)

if TYPE_CHECKING:
    from workmate.adapters.inbound.meeting_command import MeetingNoteRouter
    from workmate.adapters.inbound.responder import Responder
    from workmate.adapters.inbound.teams_graph.poller import HandleMessage
    from workmate.adapters.outbound.github_api import HttpxGithubClient
    from workmate.core.application.events import EventService
    from workmate.core.application.github import GithubWriteService
    from workmate.core.application.tools import ToolSpec
    from workmate.core.ports.github import GithubReadPort
    from workmate.core.ports.thread_links import ThreadLinkStore

logger = logging.getLogger(__name__)

_MISSING_TEAMS_GRAPH = (
    "Drzwi Teams (delegowane) wymagają extra 'teams-graph'. Zainstaluj: uv sync --extra teams-graph"
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
    # R/L1: pamięć rozmów agenta i wspólny events.db MUSZĄ być zapisywalne (tryb watch pisze oba).
    events_settings = EventsSettings.from_env()
    require_writable(events_settings.db_path, "WORKMATE_EVENTS_DB")
    require_writable(conv_settings.db_path, "WORKMATE_CONVERSATIONS_DB")
    if workspace_settings.enabled:
        # TTL sprzątanie katalogu roboczego (ADR 0018) — raz na starcie, backstop przeciw rośnięciu.
        removed = prune_stale(
            workspace_settings.workspace_dir,
            older_than=timedelta(days=workspace_settings.retention_days),
            now=datetime.now(tz=timezone.utc),
        )
        if removed:
            logger.info("Katalog roboczy: usunięto %d bezczynnych katalogów rozmów (TTL).", removed)
    extra_catalog, thread_factory = _build_bridge_catalog(
        events_settings, GithubSettings.from_env(), JiraSettings.from_env()
    )
    # ADR 0026 (A′2): dokładamy fabrykę `reply_with_file`, niezależnie bramkowaną od zapisu GitHub.
    thread_factory = _compose_thread_factories(
        thread_factory, _build_file_reply_factory(settings, token_provider)
    )
    # ADR 0027 (A′3): OSOBNE fabryki push-u 1:1 — klucz = nadawca, nie wątek. Obraz inline
    # (`send_image_to_user`) i dokument (`send_document_to_user`) są niezależnie bramkowane
    # (dokument wymaga szerszego zakresu Files.ReadWrite.All), więc składamy je w jedną fabrykę.
    user_push_factory = _compose_user_push_factories(
        _build_user_push_factory(settings, token_provider),
        _build_user_doc_push_factory(settings, token_provider),
    )
    # Produkcyjne M3 (ADR 0009 §4 / 0041): komenda ZAPISU /notatka z drzwi Teams, osobno bramkowana.
    meeting_router = _build_meeting_note_router(
        settings, token_provider, core_settings, agent_settings
    )
    responder = _build_responder(
        core_settings,
        agent_settings,
        conv_settings,
        workspace_settings,
        extra_catalog,
        thread_factory,
        user_push_factory,
        meeting_router,
    )
    handle = make_handle_message(responder)
    asyncio.run(_run(settings, token_provider, handle))


def _build_bridge_catalog(
    events_settings: EventsSettings,
    github_settings: GithubSettings,
    jira_settings: JiraSettings,
) -> tuple[list[ToolSpec], Callable[[str], list[ToolSpec]] | None]:
    """Narzędzia warstwy SPAJAJĄCEJ dla agenta Teams (ADR 0019/0021/0024/0031): zdarzenia + zapis.

    Zwraca ``(katalog, fabryka_wątkowa)``. ``read_recent_events`` jest ZAWSZE (agent widzi, co
    zdarzyło się w innych warstwach). Zapis do GitHub (issue/komentarz) i do Jiry (zgłoszenie/
    komentarz) dokładamy NIEZALEŻNIE, każdy TYLKO przy swojej włączonej bramce i skonfigurowanym
    celu — profil per drzwi (ADR 0006/0021/0031). Zdarzenia z zapisu idą jako ``source=teams``
    (strażnik pętli — notifier ich nie odeśle). Gdy zapis GitHub włączony, budujemy też FABRYKĘ
    ``reply_on_thread`` (ADR 0024, Faza 3b) dla wątku powiązanego z issue/PR.

    Propozycja czasu z commitów (ADR 0034) wchodzi BEZ bramki, gdy tylko GitHub jest
    skonfigurowany — po wycięciu ścieżki zapisu to czysty odczyt, a odczyt jest domyślny.
    """
    from workmate.adapters.outbound.sqlite_events import SqliteEventStore
    from workmate.core.application.events import EventService
    from workmate.core.application.tools import build_activity_catalog, build_events_catalog

    # Sufity zdolności mutujących Jiry (limity zapisu, wariant wdrożenia) egzekwował dotąd
    # WYŁĄCZNIE proces pollera — czyli nie ten, który wykonuje zapis. ``validate_limits``
    # to część wspólna, bezpieczna dla wdrożeń bez Jiry (nie żąda URL-a ani tokenu).
    jira_settings.validate_limits()

    events = EventService(SqliteEventStore(events_settings.db_path))
    catalog = [*build_events_catalog(events), *build_activity_catalog(events)]
    catalog += _build_jira_catalog(jira_settings, events)

    if not (github_settings.token and github_settings.owner and github_settings.repo):
        return catalog, None

    from workmate.adapters.outbound.sqlite_thread_links import SqliteThreadLinkStore
    from workmate.core.application.github import GithubWriteService
    from workmate.core.application.tools import build_github_write_catalog

    # JEDEN klient GitHub na proces, współdzielony przez odczyt commitów i zapis — dwa klienty
    # do tego samego hosta trzymałyby dwie pule połączeń bez żadnego zysku.
    client = _github_client(github_settings)
    catalog += _build_worklog_catalog(client, github_settings)
    if not github_settings.enable_github_write:
        return catalog, None

    write_service = GithubWriteService(
        client, owner=github_settings.owner, repo=github_settings.repo, events=events
    )
    thread_links = SqliteThreadLinkStore(events_settings.db_path)
    logger.info(
        "GitHub write WŁĄCZONY dla %s/%s — agent Teams może tworzyć issue/komentarze. "
        "Uwaga (ADR 0024): `reply_on_thread` zadziała TYLKO, gdy drzwi GitHub biegną z "
        "ENABLE_CHANNEL_THREADING=true na WSPÓLNYM events.db i tej samej parze team/channel — "
        "to notifier zapełnia mapę wątków. Bez tego mapa jest pusta i narzędzie wątkowe milczy.",
        github_settings.owner,
        github_settings.repo,
    )
    return (
        [*catalog, *build_github_write_catalog(write_service)],
        _make_thread_tool_factory(thread_links, write_service),
    )


def _build_jira_catalog(jira_settings: JiraSettings, events: EventService) -> list[ToolSpec]:
    """Bramkowane narzędzia Jiry: zapis (Gate 5 / ADR 0031) i tranzycja (ADR 0032) — bramki OSOBNE.

    Żadna bramka → pusto (agent bez narzędzi mutujących Jira). Co najmniej jedna → budujemy JEDEN
    klient/serwis Jiry (współdzielą PAT/URL/projekt), potem dokładamy narzędzia zapisu (create/
    comment) TYLKO przy ``enable_jira_write`` i narzędzie tranzycji TYLKO przy
    ``enable_jira_transition`` — profil per drzwi (ADR 0006/0021/0031/0032): możliwy jest profil
    „tylko-tranzycja" bez zapisu. Bramka ON, ale brak celu (token/URL/projekt) → TWARDY błąd
    (walidacja jest punktem egzekucji): cicha bramka „włączona, ale martwa" byłaby footgunem.
    ``self_account`` (strażnik pętli self-skip) egzekwuje poller Jira (``JiraSettings.validate``
    w ``workmate-jira``) — to jego proces go używa. Sync klient Jiry żyje przez proces; echo
    zapisu/tranzycji idzie jako ``source=teams`` (strażnik pętli).
    """
    if not (jira_settings.enable_jira_write or jira_settings.enable_jira_transition):
        return []
    # Wariant wdrożenia walidujemy spójnie z pollerem (``JiraSettings.validate``) — literówka w
    # DEPLOYMENT nie może po cichu zbudować klienta Server/DC (Bearer) na instancji Cloud (→ 401).
    deployment = jira_settings.deployment.strip().lower()
    if deployment not in JIRA_DEPLOYMENTS:
        raise ValueError(
            "WORKMATE_JIRA_DEPLOYMENT musi być 'server' lub 'cloud', jest: "
            f"{jira_settings.deployment!r}."
        )
    required = [
        ("WORKMATE_JIRA_TOKEN", jira_settings.token),
        ("WORKMATE_JIRA_BASE_URL", jira_settings.base_url),
        ("WORKMATE_JIRA_WRITE_PROJECT", jira_settings.write_project),
    ]
    # Cloud (ADR 0033) uwierzytelnia się Basic (email + API token); bez e-maila zapis/tranzycja z
    # Teams nie zadziała — fail-fast spójnie z resztą celów (jak poller ``JiraSettings.validate``).
    if deployment == "cloud":
        required.append(("WORKMATE_JIRA_EMAIL", jira_settings.email))
    missing = [name for name, value in required if not value]
    if missing:
        raise ValueError(
            "WORKMATE_JIRA_ENABLE_WRITE/ENABLE_TRANSITION=true wymaga: "
            + ", ".join(missing)
            + " w środowisku/.env."
        )
    # Sufit hopów (ADR 0032) egzekwujemy TU, bo to drzwi wykonujące walk — a ``validate`` (gdzie
    # też jest ten check) woła tylko poller Jira, nie te drzwi. Bez tego absurdalny cap (np. 999)
    # ominąłby twardy backstop tam, gdzie walk się dzieje. Fail-fast, nie cichy clamp.
    if jira_settings.enable_jira_transition and not (
        1 <= jira_settings.max_transition_hops <= MAX_JIRA_TRANSITION_HOPS
    ):
        raise ValueError(
            "WORKMATE_JIRA_MAX_TRANSITION_HOPS musi być w zakresie "
            f"1..{MAX_JIRA_TRANSITION_HOPS}, jest: {jira_settings.max_transition_hops}."
        )

    import atexit

    import httpx

    from workmate.adapters.outbound.jira_api import build_jira_client
    from workmate.core.application.jira import JiraWriteService
    from workmate.core.application.tools import (
        build_jira_transition_catalog,
        build_jira_write_catalog,
    )

    # Klient żyje przez cały proces (daemon), ale pulę połączeń domykamy jawnie przy wyjściu.
    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    client = build_jira_client(transport, jira_settings)
    write_service = JiraWriteService(
        client,
        project=jira_settings.write_project,
        issue_type=jira_settings.default_issue_type,
        events=events,
        max_transition_hops=jira_settings.max_transition_hops,
    )
    catalog: list[ToolSpec] = []
    if jira_settings.enable_jira_write:
        logger.info(
            "Jira write WŁĄCZONY dla projektu %s — agent Teams może tworzyć zgłoszenia/komentarze. "
            "Strażnik pętli (self-skip) domyka poller Jira: wymaga tego samego WORKMATE_JIRA_TOKEN "
            "i WORKMATE_JIRA_SELF_ACCOUNT = konto tego PAT (ADR 0031).",
            jira_settings.write_project,
        )
        catalog += build_jira_write_catalog(write_service)
    if jira_settings.enable_jira_transition:
        logger.info(
            "Jira transition WŁĄCZONY dla projektu %s (max hops=%d) — agent Teams może przesuwać "
            "status zgłoszeń. Wielo-hop (cap>1) to autonomiczna, NIEODWRACALNA mutacja; "
            "strażnik pętli jak przy zapisie (ADR 0032).",
            jira_settings.write_project,
            jira_settings.max_transition_hops,
        )
        catalog += build_jira_transition_catalog(write_service)
    return catalog


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


def _build_worklog_catalog(
    client: GithubReadPort, github_settings: GithubSettings
) -> list[ToolSpec]:
    """Narzędzie propozycji czasu z commitów (ADR 0034) — czysty ODCZYT, bez bramki.

    Bramki nie ma celowo: po wycięciu ścieżki zapisu narzędzie niczego nie mutuje, a repo trzyma
    zasadę „odczyt domyślny, bramkujemy zapis" (ADR 0006). Zdolność stoi wyłącznie na GitHubie —
    klucze Jira wyłuskujemy regexem z treści commitów, więc konfiguracja Jiry jest tu zbędna.

    Sufity estymacji egzekwujemy TU, bo ``GithubSettings.validate`` woła tylko poller GitHuba
    (``workmate-github``), a to te drzwi liczą propozycję (ta sama asymetria co przy Jirze).
    """
    from workmate.core.application.tools import build_worklog_catalog
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
    return build_worklog_catalog(service)


def _make_thread_tool_factory(
    thread_links: ThreadLinkStore, write_service: GithubWriteService
) -> Callable[[str], list[ToolSpec]]:
    """Fabryka ``reply_on_thread`` per turę (ADR 0024, Faza 3b) — analogicznie do workspace factory.

    Z ``external_id`` (``team/channel/root`` — konwencja tych drzwi) odczytuje cel wątku z
    ``ThreadLinkStore``. Gdy wątek wiąże się z issue/PR, zwraca scoped narzędzie z PRE-ZWIĄZANYM
    numerem; inaczej pusta lista (agent bez narzędzia zapisu). Numer pochodzi z zaufanego mapowania,
    nie od modelu — nie da się przekierować komentarza na inne issue.
    """
    from workmate.core.application.tools import build_thread_reply_catalog

    def factory(external_id: str) -> list[ToolSpec]:
        parts = external_id.split("/")
        if len(parts) != 3:
            return []
        team_id, channel_id, root_id = parts
        target = thread_links.get_target(team_id, channel_id, root_id)
        if target is None:
            return []
        target_kind, target_number = target
        return build_thread_reply_catalog(write_service, target_kind, target_number)

    return factory


def _build_file_reply_factory(
    settings: TeamsGraphSettings, token_provider: Callable[[], str]
) -> Callable[[str], list[ToolSpec]] | None:
    """Fabryka ``reply_with_file`` per turę (ADR 0026, A′2) — odpowiedź plikiem w wątku Teams.

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


def _build_user_push_factory(
    settings: TeamsGraphSettings, token_provider: Callable[[], str]
) -> Callable[[str], list[ToolSpec]] | None:
    """Fabryka ``send_image_to_user`` per turę (ADR 0027, A′3) — push obrazu do rozmówcy 1:1.

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
    """Fabryka ``send_document_to_user`` per turę (ADR 0027, wariant plikowy) — push pliku 1:1.

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
    # (fail-closed; config wymusił istnienie pliku). Ten sam port co worklogi; wariant plikowy
    # bez dodatkowego zakresu Graph (GraphIdentityDirectory to drop-in hardening, patrz ADR 0042).
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


def _compose_thread_factories(
    *factories: Callable[[str], list[ToolSpec]] | None,
) -> Callable[[str], list[ToolSpec]] | None:
    """Złóż kilka fabryk narzędzi wątkowych w jedną (konkatenacja wyników); ``None`` gdy żadnej.

    Responder przyjmuje JEDNĄ ``thread_tool_factory``, a jeden wątek może dostać i
    ``reply_on_thread`` (GitHub, ADR 0024), i ``reply_with_file`` (ADR 0026) — każde osobno
    bramkowane. Łączymy je, żeby obie zdolności współistniały bez zmiany kontraktu respondera.
    """
    active = [f for f in factories if f is not None]
    if not active:
        return None
    if len(active) == 1:
        return active[0]

    def combined(external_id: str) -> list[ToolSpec]:
        tools: list[ToolSpec] = []
        for factory in active:
            tools.extend(factory(external_id))
        return tools

    return combined


def _build_responder(
    core_settings: Settings,
    agent_settings: AgentSettings,
    conv_settings: ConversationSettings,
    workspace_settings: WorkspaceSettings,
    extra_catalog: list[ToolSpec],
    thread_factory: Callable[[str], list[ToolSpec]] | None = None,
    user_push_factory: Callable[[str], list[ToolSpec]] | None = None,
    meeting_router: MeetingNoteRouter | None = None,
) -> Responder:
    """Złóż respondera wspólnym builderem: katalog notatek READ-ONLY (``enable_write=False``,
    ADR 0006), ``SafeResponder`` (async), komendy read-only, kompaktowanie. Katalog roboczy
    (ADR 0018) włącza OSOBNA bramka ``enable_workspace`` (env ``WORKMATE_ENABLE_WORKSPACE``),
    niezależna od zapisu notatek. ``extra_catalog`` (ADR 0019/0021) dokłada narzędzia warstwy
    spajającej, ``thread_factory`` (ADR 0024, Faza 3b) — per-turowe ``reply_on_thread``, a
    ``user_push_factory`` (ADR 0027, A′3) — per-turowe ``send_image_to_user`` (obraz inline) oraz
    ``send_document_to_user`` (plik-załącznik) wiązane z nadawcą, niezależnie bramkowane.
    ``channel="teams_graph"`` trzyma pamięć/workspace tych drzwi osobno od bota."""
    return build_conversational_responder(
        core_settings,
        agent_settings,
        conv_settings,
        channel="teams_graph",
        enable_write=False,
        safe=True,
        enable_workspace=workspace_settings.enabled,
        workspace_settings=workspace_settings,
        extra_catalog=extra_catalog,
        thread_tool_factory=thread_factory,
        user_push_tool_factory=user_push_factory,
        meeting_notes=meeting_router,
    )


async def _run(
    settings: TeamsGraphSettings,
    token_provider: Callable[[], str],
    handle: HandleMessage,
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
