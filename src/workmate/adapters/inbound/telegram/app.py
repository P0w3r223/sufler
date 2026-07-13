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

from workmate.adapters.inbound import env
from workmate.adapters.inbound.agent_wiring import build_conversational_responder
from workmate.config import (
    AgentSettings,
    ConversationSettings,
    Settings,
    TelegramSettings,
)

logger = logging.getLogger(__name__)

_MISSING_TELEGRAM = "Drzwi Telegram wymagają extra 'telegram'. Zainstaluj: uv sync --extra telegram"


def main() -> None:
    """Uruchom proces drzwi Telegram (long polling) z runtime agenta (read-only)."""
    logging.basicConfig(level=logging.INFO)
    env.load_dotenv()

    settings = Settings.from_env()
    telegram_settings = TelegramSettings.from_env()
    telegram_settings.validate()
    agent_settings = AgentSettings.from_env()
    agent_settings.validate()
    conversation_settings = ConversationSettings.from_env()
    conversation_settings.validate()

    # Telegram = drzwi MNIEJ ZAUFANE (ADR 0006): katalog READ-ONLY (``enable_write=False``),
    # owinięte w ``SafeResponder`` (łagodna degradacja). Recepta pamięci + komend read-only
    # ze wspólnego buildera (brak extra ``agent`` → czytelny SystemExit z buildera).
    responder = build_conversational_responder(
        settings,
        agent_settings,
        conversation_settings,
        channel="telegram",
        enable_write=False,
        safe=True,
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
