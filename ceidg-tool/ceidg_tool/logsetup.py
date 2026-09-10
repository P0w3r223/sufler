"""Logowanie do pliku w katalogu danych użytkownika z maskowaniem tokenów JWT."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import mask_tokens

LOG_FILE_NAME = "ceidg-tool.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3
LOGGER_NAME = "ceidg_tool"


class MaskingFormatter(logging.Formatter):
    """Formatter, który po sformatowaniu wpisu zastępuje każdy JWT znacznikiem `<token>`."""

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
    for handler in list(logger.handlers):
        if getattr(handler, "_ceidg_file", False):
            logger.removeHandler(handler)
            handler.close()
    handler = RotatingFileHandler(
        path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(
        MaskingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S")
    )
    handler._ceidg_file = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.propagate = False
    return path


def close_file_handlers() -> None:
    """Zwalnia uchwyt do pliku logu — Windows nie pozwala usunąć otwartego pliku."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        if getattr(handler, "_ceidg_file", False):
            logger.removeHandler(handler)
            handler.close()


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(LOGGER_NAME if name is None else f"{LOGGER_NAME}.{name}")
