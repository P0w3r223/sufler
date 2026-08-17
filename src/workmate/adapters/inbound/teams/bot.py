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
            sender_id=_sender_aad_id(activity),
            conversation_id=_conversation_of(activity),
            mentions_bot=_mentions_bot(activity),
        )
        reply = await responder.respond(message)
        await context.send_activity(reply)

    return on_message


def _sender_of(activity: Any) -> str:
    """Nazwa nadawcy do ATRYBUCJI (log, klucz zastępczy wątku) — nie do autoryzacji.

    ``name``/``id`` pochodzą z Bot Framework (``29:…``), więc nie mają nic wspólnego z AAD
    i żadna bramka nie ma prawa ich czytać — od tego jest ``_sender_aad_id`` niżej.
    """
    sender = getattr(activity, "from_property", None)
    return _text(_attr(sender, "name")) or _text(_attr(sender, "id"))


def _sender_aad_id(activity: Any) -> str:
    """AAD object id nadawcy — JEDYNA tożsamość, którą wolno podać bramkom (ADR 0042/0062/0063).

    Bot Framework niesie go w ``activity.from.aadObjectId`` (SDK: ``from_property.aad_object_id``)
    i jest to ten sam identyfikator, którym posługuje się mapa tożsamości oraz drzwi delegowane
    (Graph ``from.user.id``). BEZ fallbacku na ``id``/``name``: identyfikator kanału (``29:…``)
    nigdy nie rozwiąże się w mapie, więc podstawienie go tutaj dałoby tożsamość FAŁSZYWĄ zamiast
    braku tożsamości. Gość, konto spoza tenantu i aktywność systemowa nie mają ``aadObjectId`` —
    zostaje pusty napis, czyli „nadawca nierozpoznany", i wszystkie bramki sender-keyed odmawiają
    (fail-closed).
    """
    return _text(_attr(getattr(activity, "from_property", None), "aad_object_id"))


def _conversation_of(activity: Any) -> str:
    conversation = getattr(activity, "conversation", None)
    return _text(_attr(conversation, "id"))


def _mentions_bot(activity: Any) -> bool:
    """Czy wiadomość @wzmiankuje TEGO bota — wyzwalacz dyrektyw (ADR 0048/0051/0052).

    Bot Framework dokłada wzmianki jako encje ``{type: 'mention', mentioned: {id}}``, a własne
    konto bota w tej rozmowie to ``activity.recipient.id`` (postać ``28:<app-id>``) — porównanie
    z nim jest jedynym pewnym sygnałem, że wzmianka celuje w nas, a nie w innego uczestnika.
    Bez ``recipient`` (Emulator bywa oszczędny) zwracamy ``False``: nie ma z czym porównać, więc
    wyzwalacz ma milczeć, a nie zgadywać.
    """
    bot_id = _text(_attr(getattr(activity, "recipient", None), "id"))
    if not bot_id:
        return False
    for entity in getattr(activity, "entities", None) or ():
        if _text(_attr(entity, "type")).lower() != "mention":
            continue
        if _text(_attr(_attr(entity, "mentioned"), "id")) == bot_id:
            return True
    return False


def _attr(obj: Any, name: str) -> Any:
    """Pole aktywności niezależnie od tego, czym jest — modelem SDK, słownikiem czy atrapą.

    Encje aktywności bywają deserializowane do generycznego ``Entity`` z nierozpoznanymi polami
    w ``additional_properties``; wzmianka jest właśnie takim przypadkiem, więc czytamy oba miejsca.
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    value = getattr(obj, name, None)
    if value is None:
        extra = getattr(obj, "additional_properties", None)
        if isinstance(extra, dict):
            return extra.get(name)
    return value


def _text(value: Any) -> str:
    """Napis albo pusty — ``None`` i typy nietekstowe z SDK nie wchodzą do ``InboundMessage``."""
    return value.strip() if isinstance(value, str) else ""


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
