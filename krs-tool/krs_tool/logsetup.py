"""Logowanie do pliku w katalogu danych użytkownika, z maskowaniem.

Skopiowane z `ceidg-tool/ceidg_tool/logsetup.py` (kopia z 2026-09-10); zmienione stałe
i źródło `mask_tokens`. O tym, że rejestr sekretów jest dziś pusty, mówi `secrets.py` —
formatter zostaje, bo szew ma istnieć, zanim będzie co maskować.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .secrets import mask_tokens

LOG_FILE_NAME = "krs-tool.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3
LOGGER_NAME = "krs_tool"
_HANDLER_MARKER = "_krs_file"


class MaskingFormatter(logging.Formatter):
    """Formatter, który po sformatowaniu wpisu maskuje każdy sekret."""

    def format(self, record: logging.LogRecord) -> str:
        return mask_tokens(super().format(record))

    def formatException(self, ei: object) -> str:  # noqa: N802 - nazwa z logging
        return mask_tokens(super().formatException(ei))  # type: ignore[arg-type]


def setup_logging(log_dir: Path, *, level: int = logging.INFO) -> Path:
    """Konfiguruje logger pakietu; zwraca ścieżkę pliku logu."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / LOG_FILE_NAME
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    close_file_handlers()
    handler = RotatingFileHandler(
        path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(
        MaskingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S")
    )
    setattr(handler, _HANDLER_MARKER, True)
    logger.addHandler(handler)
    logger.propagate = False
    return path


def close_file_handlers() -> None:
    """Zwalnia uchwyt do pliku logu — Windows nie pozwala usunąć otwartego pliku."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(LOGGER_NAME if name is None else f"{LOGGER_NAME}.{name}")
