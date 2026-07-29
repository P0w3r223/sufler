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
    require_writable,
)

logger = logging.getLogger(__name__)

_MISSING_TELEGRAM = "Drzwi Telegram wymagają extra 'telegram'. Zainstaluj: uv sync --extra telegram"


def main() -> None:
    """Uruchom proces drzwi Telegram (long polling) z runtime agenta (read-only)."""
    env.load_dotenv()
    env.configure_logging()

    settings = Settings.from_env()
    telegram_settings = TelegramSettings.from_env()
    telegram_settings.validate()
    agent_settings = AgentSettings.from_env()
    agent_settings.validate()
    conversation_settings = ConversationSettings.from_env()
    conversation_settings.validate()
    # R/L1: baza rozmów agenta na wolumenie MUSI być zapisywalna — inaczej pamięć leci w próżnię
    # (na koncie kontenera z niezapisywalnym ~). Fail-fast na starcie, nie przy pierwszej rozmowie.
    require_writable(conversation_settings.db_path, "WORKMATE_CONVERSATIONS_DB")

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
        "Ctrl+C/SIGTERM, aby zatrzymać."
    )
    # Graceful shutdown (R1): własnego handlera SIGTERM tu NIE instalujemy — ``run_polling`` z
    # python-telegram-bot sam łapie SIGINT/SIGTERM/SIGABRT i domyka pętlę czysto. Drzwi są
    # READ-ONLY (brak pliku stanu do utrwalenia), więc nie ma czego zapisać przed wyjściem.
    app.run_polling()


if __name__ == "__main__":
    main()
