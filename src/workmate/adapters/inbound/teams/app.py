"""Entry point procesu drzwi Teams (Faza 2) — osobny proces obok serwera MCP.

Uruchomienie: ``uv run workmate-teams``. Lokalny test bez Azure:
``WORKMATE_TEAMS_ANONYMOUS=true`` + Bot Framework Emulator na porcie 3978.
Realny test w Teams: pełne ``WORKMATE_TEAMS_*`` (single-tenant) + dev tunnel.

Importy SDK/aiohttp są leniwe (w funkcjach), a brak extra ``teams`` kończy się
czytelnym komunikatem, nie surowym ``ImportError``.
"""
from __future__ import annotations

import logging
from typing import Any

from workmate.adapters.inbound.responder import EchoResponder, Responder
from workmate.config import TeamsSettings

logger = logging.getLogger(__name__)


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
    """Uruchom proces drzwi Teams."""
    logging.basicConfig(level=logging.INFO)
    settings = TeamsSettings.from_env()
    settings.validate()

    # Punkt szwu M1: dziś EchoResponder; później RuntimeResponder(rdzeń) — jedna linia.
    responder: Responder = EchoResponder()

    try:
        from aiohttp import web

        app = build_web_app(settings, responder)
    except ImportError as exc:
        raise SystemExit(
            "Drzwi Teams wymagają extra 'teams'. Zainstaluj: uv sync --extra teams"
        ) from exc

    logger.info(
        "Drzwi Teams nasłuchują na http://%s:%s/api/messages (anonymous=%s)",
        settings.bind_host,
        settings.bind_port,
        settings.anonymous_auth,
    )
    web.run_app(app, host=settings.bind_host, port=settings.bind_port)


if __name__ == "__main__":
    main()
