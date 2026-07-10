"""Entry point drzwi Telegram (Faza 2) — long polling, bez Azure/tunelu.

Uruchomienie: ``uv run workmate-telegram`` (wymaga: ``uv sync --extra telegram --extra agent``).
Bot odpowiada RUNTIME AGENTA rdzenia nad tymi samymi narzędziami co drzwi MCP — ale
Telegram to drzwi MNIEJ ZAUFANE (ADR 0006), więc katalog jest READ-ONLY (agent czyta
notatki i status, nie zapisuje). Sekrety wyłącznie z env (ładowane też z ``.env`` przez
python-dotenv): ``WORKMATE_TELEGRAM_BOT_TOKEN`` + ``ANTHROPIC_API_KEY``; brak → twardy błąd.

Bez webhooka i bez publicznego endpointu — bot sam odpytuje Telegram (long polling).
Importy SDK/dotenv są leniwe; brak extra kończy się czytelnym komunikatem.

Powrót do samego echa (bez API/klucza) to jedna linia: ``RuntimeResponder(runtime)``
→ ``EchoResponder()`` (patrz ``adapters/inbound/responder.py``).
"""
from __future__ import annotations

import logging

from workmate.adapters.inbound.responder import (
    ConversationalResponder,
    Responder,
    SafeResponder,
)
from workmate.adapters.outbound.sqlite_conversations import SqliteConversationStore
from workmate.config import (
    AgentSettings,
    ConversationSettings,
    Settings,
    TelegramSettings,
)
from workmate.core.application.conversations import ConversationService

logger = logging.getLogger(__name__)

_MISSING_TELEGRAM = "Drzwi Telegram wymagają extra 'telegram'. Zainstaluj: uv sync --extra telegram"
_MISSING_AGENT = "Runtime agenta wymaga extra 'agent'. Zainstaluj: uv sync --extra agent"


def main() -> None:
    """Uruchom proces drzwi Telegram (long polling) z runtime agenta (read-only)."""
    logging.basicConfig(level=logging.INFO)

    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise SystemExit(_MISSING_TELEGRAM) from exc
    load_dotenv()

    settings = Settings.from_env()
    telegram_settings = TelegramSettings.from_env()
    telegram_settings.validate()
    agent_settings = AgentSettings.from_env()
    agent_settings.validate()

    # Telegram = drzwi MNIEJ ZAUFANE (ADR 0006): runtime na katalogu READ-ONLY.
    from workmate.adapters.inbound.agent_wiring import build_agent_runtime

    try:
        runtime = build_agent_runtime(settings, agent_settings, enable_write=False)
    except ImportError as exc:
        raise SystemExit(_MISSING_AGENT) from exc

    # Pamięć rozmów (ADR 0010): wątkowość per czat + limit kontekstu z rollover.
    conversation_settings = ConversationSettings.from_env()
    conversation_settings.validate()
    conversations = ConversationService(
        SqliteConversationStore(conversation_settings.db_path),
        max_context_tokens=conversation_settings.max_context_tokens,
        idle_timeout=conversation_settings.idle_timeout(),
    )
    # SafeResponder: łagodna degradacja przy błędach runtime/infra (odporność drzwi).
    responder: Responder = SafeResponder(
        ConversationalResponder(runtime, conversations, channel="telegram")
    )

    from workmate.adapters.inbound.telegram.bot import build_application

    try:
        app = build_application(telegram_settings, responder)
    except ImportError as exc:
        raise SystemExit(_MISSING_TELEGRAM) from exc

    logger.info(
        "Drzwi Telegram wystartowały (long polling, runtime agenta read-only). "
        "Ctrl+C, aby zatrzymać."
    )
    app.run_polling()


if __name__ == "__main__":
    main()
