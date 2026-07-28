"""Handler wiadomości kanału → responder (szew). Wolny od SDK/httpx, testowalny atrapą.

Analogicznie do ``teams/bot.py::make_on_message`` i ``telegram/bot.py`` — z drzwi
wychodzi jedynie ``InboundMessage``, a co bot odpowiada, decyduje wstrzyknięty
``Responder`` (echo / runtime agenta / … — podmiana to jedna linia w ``app.py``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from workmate.adapters.inbound.responder import InboundMessage, Responder
from workmate.adapters.inbound.teams_graph.selection import ChannelMessage


def make_handle_message(
    responder: Responder,
) -> Callable[[ChannelMessage, str], Awaitable[str | None]]:
    """Zbuduj handler: z ``ChannelMessage`` składa ``InboundMessage`` i woła responder.

    ``conversation_id`` (klucz PAMIĘCI wątku) podaje poller — to ``team/channel/root``,
    więc każdy wątek kanału ma osobną historię w ``ConversationService``. Pusta odpowiedź
    mapuje się na ``None`` (poller nie wysyła nic).
    """

    async def handle(message: ChannelMessage, conversation_id: str) -> str | None:
        reply = await responder.respond(
            InboundMessage(
                text=message.text,
                sender=message.sender_name,
                sender_id=message.sender_id,
                conversation_id=conversation_id,
                attachments=message.attachments,
            )
        )
        return reply or None

    return handle
