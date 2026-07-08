"""Handler wiadomości i budowa bota Teams (Faza 2, spike M2).

Świadomy podział: ``make_on_message`` (logika handlera) jest wolne od importów SDK
w runtime i testowalne atrapą ``TurnContext``; ``build_agent_app`` (wiring)
importuje Microsoft 365 Agents SDK LENIWIE, więc sam import tego modułu oraz testy
handlera działają bez zainstalowanego extra ``teams``.

API zweryfikowane wobec Agents SDK 1.1.0 (patrz docs/research/teams-bot-setup-2026.md):
trasa aiohttp woła ``start_agent_process(request, agent_app, adapter)``.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from workmate.adapters.inbound.responder import InboundMessage, Responder

if TYPE_CHECKING:
    from workmate.config import TeamsSettings


def make_on_message(responder: Responder) -> Callable[[Any, Any], Awaitable[None]]:
    """Zbuduj asynchroniczny handler wiadomości, wstrzykując responder (szew).

    ``context`` jest kaczo-typowany (``.activity.text``, ``.send_activity``), dzięki
    czemu handler testujemy atrapą ``TurnContext`` bez zainstalowanego SDK.
    """

    async def on_message(context: Any, _state: Any = None) -> None:
        activity = context.activity
        message = InboundMessage(
            text=getattr(activity, "text", "") or "",
            sender=_sender_of(activity),
            conversation_id=_conversation_of(activity),
        )
        reply = await responder.respond(message)
        await context.send_activity(reply)

    return on_message


def _sender_of(activity: Any) -> str:
    sender = getattr(activity, "from_property", None)
    return getattr(sender, "name", "") or getattr(sender, "id", "") or ""


def _conversation_of(activity: Any) -> str:
    conversation = getattr(activity, "conversation", None)
    return getattr(conversation, "id", "") or ""


def build_agent_app(settings: TeamsSettings, responder: Responder) -> tuple[Any, Any, Any]:
    """Zbuduj ``(adapter, agent_app, auth_config)`` Agents SDK. Importy SDK leniwe.

    Zwraca też ``auth_config``, bo middleware aiohttp (``jwt_authorization_middleware``)
    czyta go z ``app['agent_configuration']`` — patrz ``app.py``. W trybie anonimowym
    (lokalny Emulator) puste ID/sekret + ``anonymous_allowed=True`` wystarczają.
    """
    from microsoft_agents.authentication.msal import MsalConnectionManager
    from microsoft_agents.hosting.aiohttp import CloudAdapter
    from microsoft_agents.hosting.core import (
        AgentApplication,
        AgentAuthConfiguration,
        ApplicationOptions,
        AuthTypes,
        MemoryStorage,
    )

    auth_config = AgentAuthConfiguration(
        auth_type=AuthTypes.client_secret,
        client_id=settings.app_id or None,
        client_secret=settings.app_password or None,
        tenant_id=settings.tenant_id or None,
        anonymous_allowed=settings.anonymous_auth,
    )
    connection_manager = MsalConnectionManager(
        connections_configurations={"SERVICE_CONNECTION": auth_config}
    )
    adapter = CloudAdapter(connection_manager=connection_manager)
    options = ApplicationOptions(
        adapter=adapter, bot_app_id=settings.app_id, storage=MemoryStorage()
    )
    agent_app = AgentApplication(options, connection_manager=connection_manager)
    # Rejestracja przez wywołanie dekoratora jako funkcji (bez składni @), żeby sam
    # import modułu nie wymagał SDK. Routujemy po TYPIE aktywności 'message'.
    agent_app.activity("message")(make_on_message(responder))
    return adapter, agent_app, auth_config
