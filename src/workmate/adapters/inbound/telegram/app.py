"""Entry point drzwi Telegram (Faza 2, spike echo) — long polling, bez Azure/tunelu.

Uruchomienie: ``uv run workmate-telegram`` (wymaga: ``uv sync --extra telegram``).
Token WYŁĄCZNIE z env ``WORKMATE_TELEGRAM_BOT_TOKEN`` (ładowany też z ``.env`` przez
python-dotenv). Bez webhooka i bez publicznego endpointu — bot sam odpytuje
Telegram (``app.run_polling()``).

Importy SDK/dotenv są leniwe; brak extra ``telegram`` kończy się czytelnym
komunikatem, nie surowym ``ImportError``.
"""
from __future__ import annotations

import logging

from workmate.adapters.inbound.responder import EchoResponder, Responder
from workmate.config import TelegramSettings

logger = logging.getLogger(__name__)

_MISSING_EXTRA = "Drzwi Telegram wymagają extra 'telegram'. Zainstaluj: uv sync --extra telegram"


def main() -> None:
    """Uruchom proces drzwi Telegram (long polling)."""
    logging.basicConfig(level=logging.INFO)

    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise SystemExit(_MISSING_EXTRA) from exc
    load_dotenv()

    settings = TelegramSettings.from_env()
    settings.validate()

    # Punkt szwu: dziś EchoResponder; później RuntimeResponder(rdzeń) / save_note — jedna linia.
    responder: Responder = EchoResponder()

    from workmate.adapters.inbound.telegram.bot import build_application

    try:
        app = build_application(settings, responder)
    except ImportError as exc:
        raise SystemExit(_MISSING_EXTRA) from exc

    logger.info("Drzwi Telegram wystartowały (long polling). Ctrl+C, aby zatrzymać.")
    app.run_polling()


if __name__ == "__main__":
    main()
