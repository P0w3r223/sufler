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
    Settings,
    TeamsGraphSettings,
    WorkspaceSettings,
)

if TYPE_CHECKING:
    from workmate.adapters.inbound.responder import Responder
    from workmate.adapters.inbound.teams_graph.poller import HandleMessage

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
    responder = _build_responder(
        core_settings, agent_settings, conv_settings, workspace_settings
    )
    handle = make_handle_message(responder)
    asyncio.run(_run(settings, token_provider, handle))


def _build_responder(
    core_settings: Settings,
    agent_settings: AgentSettings,
    conv_settings: ConversationSettings,
    workspace_settings: WorkspaceSettings,
) -> Responder:
    """Złóż respondera wspólnym builderem: katalog notatek READ-ONLY (``enable_write=False``,
    ADR 0006), ``SafeResponder`` (async), komendy read-only, kompaktowanie. Katalog roboczy
    (ADR 0018) włącza OSOBNA bramka ``enable_workspace`` (env ``WORKMATE_ENABLE_WORKSPACE``),
    niezależna od zapisu notatek. ``channel="teams_graph"`` trzyma pamięć/workspace tych drzwi
    osobno od drzwi bota (``"teams"``)."""
    return build_conversational_responder(
        core_settings,
        agent_settings,
        conv_settings,
        channel="teams_graph",
        enable_write=False,
        safe=True,
        enable_workspace=workspace_settings.enabled,
        workspace_settings=workspace_settings,
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
