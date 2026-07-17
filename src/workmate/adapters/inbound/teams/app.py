"""Entry point procesu drzwi Teams (Faza 2) — osobny proces obok serwera MCP.

Bot odpowiada RUNTIME AGENTA rdzenia nad tymi samymi narzędziami co drzwi MCP —
ale Teams to drzwi MNIEJ ZAUFANE (ADR 0006), więc katalog jest READ-ONLY (agent
czyta notatki i status, nie zapisuje). Wymaga: ``uv sync --extra teams --extra agent``
oraz ``ANTHROPIC_API_KEY`` w środowisku (brak → twardy błąd startu).

Uruchomienie: ``uv run workmate-teams``. Lokalny test bez Azure:
``WORKMATE_TEAMS_ANONYMOUS=true`` + Bot Framework Emulator na porcie 3978.
Realny test w Teams: pełne ``WORKMATE_TEAMS_*`` (single-tenant) + dev tunnel.

Importy SDK/aiohttp/Anthropic są leniwe (w funkcjach), a brak extra kończy się
czytelnym komunikatem, nie surowym ``ImportError``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from workmate.adapters.inbound import env
from workmate.adapters.inbound.agent_wiring import build_conversational_responder
from workmate.config import (
    AgentSettings,
    ConversationSettings,
    Settings,
    TeamsSettings,
)

if TYPE_CHECKING:
    from workmate.adapters.inbound.responder import Responder

logger = logging.getLogger(__name__)

_MISSING_TEAMS = "Drzwi Teams wymagają extra 'teams'. Zainstaluj: uv sync --extra teams"


def build_web_app(settings: TeamsSettings, responder: Responder) -> Any:
    """Zbuduj aplikację aiohttp z trasą ``/api/messages`` i middlewarem JWT."""
    from aiohttp import web
    from microsoft_agents.hosting.aiohttp import (
        jwt_authorization_middleware,
        start_agent_process,
    )

    from workmate.adapters.inbound.teams.bot import build_agent_app

    adapter, agent_app, auth_config = build_agent_app(settings, responder)

    async def messages(request: Any) -> Any:
        response = await start_agent_process(request, agent_app, adapter)
        # start_agent_process bywa None (odpowiedź poszła kanałem connectora) —
        # aiohttp wymaga obiektu Response, więc domykamy 201.
        return response or web.Response(status=201)

    app = web.Application(middlewares=[jwt_authorization_middleware])
    # jwt_authorization_middleware czyta konfigurację auth stąd (tryb anonimowy/JWT).
    app["agent_configuration"] = auth_config
    app.router.add_post("/api/messages", messages)
    return app


def main() -> None:
    """Uruchom proces drzwi Teams z runtime agenta (katalog read-only)."""
    logging.basicConfig(level=logging.INFO)
    env.load_dotenv()
    settings = TeamsSettings.from_env()
    settings.validate()

    # Teams = drzwi MNIEJ ZAUFANE (ADR 0006): katalog READ-ONLY (``enable_write=False``) —
    # agent czyta notatki i status, nie zapisuje. Wymaga ANTHROPIC_API_KEY (brak → czytelny
    # SystemExit z buildera). Recepta pamięci + komend read-only ze wspólnego buildera.
    core_settings = Settings.from_env()
    agent_settings = AgentSettings.from_env()
    agent_settings.validate()
    conversation_settings = ConversationSettings.from_env()
    conversation_settings.validate()
    responder: Responder = build_conversational_responder(
        core_settings,
        agent_settings,
        conversation_settings,
        channel="teams",
        enable_write=False,
        safe=True,
    )

    try:
        from aiohttp import web

        app = build_web_app(settings, responder)
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS) from exc

    logger.info(
        "Drzwi Teams nasłuchują na http://%s:%s/api/messages "
        "(anonymous=%s, runtime agenta read-only)",
        settings.bind_host,
        settings.bind_port,
        settings.anonymous_auth,
    )
    web.run_app(app, host=settings.bind_host, port=settings.bind_port)


if __name__ == "__main__":
    main()
