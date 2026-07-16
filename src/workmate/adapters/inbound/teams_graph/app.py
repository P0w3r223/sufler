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
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from workmate.adapters.inbound import env
from workmate.adapters.inbound.agent_wiring import build_conversational_responder
from workmate.adapters.inbound.teams_graph.handler import make_handle_message
from workmate.adapters.outbound.filesystem_workspace import prune_stale
from workmate.config import (
    AgentSettings,
    ConversationSettings,
    EventsSettings,
    GithubSettings,
    Settings,
    TeamsGraphSettings,
    WorkspaceSettings,
)

if TYPE_CHECKING:
    from workmate.adapters.inbound.responder import Responder
    from workmate.adapters.inbound.teams_graph.poller import HandleMessage
    from workmate.core.application.github import GithubWriteService
    from workmate.core.application.tools import ToolSpec
    from workmate.core.ports.thread_links import ThreadLinkStore

logger = logging.getLogger(__name__)

_MISSING_TEAMS_GRAPH = (
    "Drzwi Teams (delegowane) wymagają extra 'teams-graph'. "
    "Zainstaluj: uv sync --extra teams-graph"
)


def main() -> None:
    """Uruchom proces drzwi Teams (delegowany polling) z runtime agenta (read-only)."""
    logging.basicConfig(level=logging.INFO)
    env.load_dotenv()

    settings = TeamsGraphSettings.from_env()
    settings.validate()

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
        EventsSettings.from_env(), GithubSettings.from_env()
    )
    responder = _build_responder(
        core_settings, agent_settings, conv_settings, workspace_settings,
        extra_catalog, thread_factory,
    )
    handle = make_handle_message(responder)
    asyncio.run(_run(settings, token_provider, handle))


def _build_bridge_catalog(
    events_settings: EventsSettings, github_settings: GithubSettings
) -> tuple[list[ToolSpec], Callable[[str], list[ToolSpec]] | None]:
    """Narzędzia warstwy SPAJAJĄCEJ dla agenta Teams (ADR 0019/0021/0024): zdarzenia + zapis GitHub.

    Zwraca ``(katalog, fabryka_wątkowa)``. ``read_recent_events`` jest ZAWSZE (agent widzi, co
    zdarzyło się w innych warstwach). Zapis do GitHub (issue/komentarz) dokładamy TYLKO przy
    włączonej bramce ``enable_github_write`` i skonfigurowanym repo/tokenie — profil per drzwi
    (ADR 0006/0021). Zdarzenia z zapisu idą jako ``source=teams`` (strażnik pętli — notifier ich nie
    odeśle). Gdy zapis włączony, budujemy też FABRYKĘ ``reply_on_thread`` (ADR 0024, Faza 3b): dla
    wątku powiązanego z issue/PR wstrzyknie scoped narzędzie z numerem celu.
    """
    from workmate.adapters.outbound.sqlite_events import SqliteEventStore
    from workmate.core.application.events import EventService
    from workmate.core.application.tools import build_events_catalog

    events = EventService(SqliteEventStore(events_settings.db_path))
    catalog = build_events_catalog(events)

    if not (
        github_settings.enable_github_write
        and github_settings.token
        and github_settings.owner
        and github_settings.repo
    ):
        return catalog, None

    import httpx

    from workmate.adapters.outbound.github_api import HttpxGithubClient
    from workmate.adapters.outbound.sqlite_thread_links import SqliteThreadLinkStore
    from workmate.core.application.github import GithubWriteService
    from workmate.core.application.tools import build_github_write_catalog

    # Sync klient GitHub żyje przez cały proces (daemon); tool dispatch woła go w wątkach puli.
    client = HttpxGithubClient(
        httpx.Client(timeout=30), github_settings.token, api_base=github_settings.api_base
    )
    write_service = GithubWriteService(
        client, owner=github_settings.owner, repo=github_settings.repo, events=events
    )
    thread_links = SqliteThreadLinkStore(events_settings.db_path)
    logger.info(
        "GitHub write WŁĄCZONY dla %s/%s — agent Teams może tworzyć issue/komentarze.",
        github_settings.owner,
        github_settings.repo,
    )
    return (
        [*catalog, *build_github_write_catalog(write_service)],
        _make_thread_tool_factory(thread_links, write_service),
    )


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


def _build_responder(
    core_settings: Settings,
    agent_settings: AgentSettings,
    conv_settings: ConversationSettings,
    workspace_settings: WorkspaceSettings,
    extra_catalog: list[ToolSpec],
    thread_factory: Callable[[str], list[ToolSpec]] | None = None,
) -> Responder:
    """Złóż respondera wspólnym builderem: katalog notatek READ-ONLY (``enable_write=False``,
    ADR 0006), ``SafeResponder`` (async), komendy read-only, kompaktowanie. Katalog roboczy
    (ADR 0018) włącza OSOBNA bramka ``enable_workspace`` (env ``WORKMATE_ENABLE_WORKSPACE``),
    niezależna od zapisu notatek. ``extra_catalog`` (ADR 0019/0021) dokłada narzędzia warstwy
    spajającej, a ``thread_factory`` (ADR 0024, Faza 3b) — per-turowe ``reply_on_thread``.
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
    from workmate.adapters.inbound.teams_graph import state as state_store
    from workmate.adapters.inbound.teams_graph.attachments import (
        AttachmentLimits,
        AttachmentMaterializer,
    )
    from workmate.adapters.inbound.teams_graph.poller import ChannelPoller

    initial_state = state_store.load(settings.state_path)
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
        )
        await poller.run()


async def _discover(
    settings: TeamsGraphSettings, token_provider: Callable[[], str]
) -> None:
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
