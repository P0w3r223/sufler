"""Handler wiadomości i budowa aplikacji bota Telegram (Faza 2, spike echo).

Świadomy podział jak w drzwiach Teams: ``make_on_message`` (logika handlera) jest
wolne od importów SDK i testowalne atrapą ``Update``; ``build_application`` (wiring)
importuje python-telegram-bot LENIWIE, więc sam import tego modułu oraz testy
handlera działają bez zainstalowanego extra ``telegram``.

Tryb LONG POLLING (``app.run_polling()``) — bez webhooka, bez publicznego endpointu.
Treść z Telegrama to DANE, nie polecenia; drzwi mniej zaufane (przy wpięciu runtime
katalog read-only, ADR 0006).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from workmate.adapters.inbound.responder import InboundMessage, Responder

if TYPE_CHECKING:
    from workmate.config import TelegramSettings


def make_on_message(responder: Responder) -> Callable[[Any, Any], Awaitable[None]]:
    """Zbuduj asynchroniczny handler wiadomości, wstrzykując responder (szew).

    ``update`` jest kaczo-typowany (``.effective_message.text``, ``.reply_text``),
    dzięki czemu handler testujemy atrapą ``Update`` bez zainstalowanego SDK.
    """

    async def on_message(update: Any, _context: Any = None) -> None:
        message = getattr(update, "effective_message", None)
        if message is None:
            return  # aktualizacja nie-wiadomościowa — nie ma na co odpowiedzieć
        text = getattr(message, "text", "") or ""
        inbound = InboundMessage(
            text=text,
            sender=_sender_of(update),
            conversation_id=_chat_of(update),
        )
        reply = await responder.respond(inbound)
        await message.reply_text(reply)

    return on_message


def _sender_of(update: Any) -> str:
    user = getattr(update, "effective_user", None)
    if user is None:
        return ""
    return (
        getattr(user, "username", "")
        or getattr(user, "full_name", "")
        or str(getattr(user, "id", "") or "")
    )


def _chat_of(update: Any) -> str:
    chat = getattr(update, "effective_chat", None)
    return str(getattr(chat, "id", "") or "") if chat is not None else ""


def build_application(settings: TelegramSettings, responder: Responder) -> Any:
    """Zbuduj aplikację python-telegram-bot z handlerem tekstu. Import PTB leniwy.

    Rejestruje handler na wiadomościach tekstowych (bez komend). Zwrócony obiekt
    ``Application`` uruchamia się przez ``app.run_polling()`` (patrz ``app.py``).
    """
    from telegram.ext import Application, MessageHandler, filters

    application = Application.builder().token(settings.bot_token).build()
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, make_on_message(responder))
    )
    return application
