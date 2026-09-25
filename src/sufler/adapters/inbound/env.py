"""Wspólne wczytywanie ``.env`` dla drzwi inbound (CLI, Teams, Teams-Graph).

Jedno, odporne na kodowanie źródło zamiast czterech różnych podejść w ``*/app.py``.
Bez zależności zewnętrznej (nie ``python-dotenv``): realne zmienne środowiskowe zawsze
mają priorytet (``setdefault``). PowerShell domyślnie zapisuje UTF-16 LE z BOM — obsługujemy
oba warianty, żeby ``.env`` z Windowsa działał tak samo jak z powłoki uniksowej.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from pathlib import Path


def configure_logging(level: str | None = None) -> None:
    """Skonfiguruj root logger wg ``SUFLER_LOG_LEVEL`` (domyślnie ``INFO``).

    Wspólne dla drzwi inbound — inaczej każdy entrypoint zaszywa ``INFO`` na sztywno i
    ``SUFLER_LOG_LEVEL=DEBUG`` nie ma efektu (a serwer HTTP już bierze poziom z
    ``settings.log_level``, ``server.py``). Wołać PO ``load_dotenv``, żeby poziom z ``.env``
    również zadziałał; ta sama zmienna, którą czyta ``Settings.log_level`` (``config/server.py``).
    """
    resolved = (level or os.environ.get("SUFLER_LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(level=resolved)


def force_utf8_io() -> None:
    """Wymuś UTF-8 na stdin/stdout/stderr — inaczej polskie znaki psują się w konsoli.

    Python na Windows domyślnie pisze w kodowaniu lokalnym (cp1250), a nowoczesne
    terminale (Git Bash/MinTTY, Windows Terminal, VS Code) renderują UTF-8 — stąd
    „krzaki" w diakrytykach. ``reconfigure`` jest bezpieczne i idempotentne; strumienie
    bez tej metody (np. atrapa w testach) po cichu pomijamy. Wspólne dla drzwi inbound,
    które piszą po polsku do konsoli (CLI, harness M3).
    """
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8")


def apply_env_file(env_file: Path) -> None:
    """Wczytaj plik ``.env`` do ``os.environ`` (``setdefault`` — realne env wygrywa).

    Odporność na kodowanie: ``utf-8-sig`` obsługuje UTF-8 z/bez BOM, gałąź UTF-16 —
    pliki zapisane przez PowerShell (UTF-16 LE z BOM).
    """
    data = env_file.read_bytes()
    try:
        if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
            text = data.decode("utf-16")
        else:
            text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SystemExit(
            f"Nie udało się odczytać {env_file.name} — sprawdź kodowanie (zapisz jako UTF-8): {exc}"
        ) from exc
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_dotenv() -> None:
    """Znajdź repo-lokalny ``.env`` (korzeń z ``pyproject.toml``) i wczytaj go, jeśli jest.

    Wygoda deva: klucz i ustawienia można trzymać w ``.env`` (w ``.gitignore``) zamiast
    eksportować ręcznie. Brak pliku nie jest błędem (operator może eksportować w powłoce).
    """
    here = Path(__file__).resolve()
    root = next((p for p in (here, *here.parents) if (p / "pyproject.toml").is_file()), None)
    if root is not None and (root / ".env").is_file():
        apply_env_file(root / ".env")
