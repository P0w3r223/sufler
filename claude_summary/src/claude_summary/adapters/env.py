"""Wczytywanie ``.env`` bez zależności zewnętrznej (wzorzec z WorkMate ``inbound/env.py``).

Realne zmienne środowiskowe zawsze mają priorytet (``setdefault``). PowerShell domyślnie
zapisuje UTF-16 LE z BOM — obsługujemy oba warianty, żeby ``.env`` z Windowsa działał tak
samo jak z powłoki uniksowej.
"""

from __future__ import annotations

import os
from pathlib import Path


def apply_env_file(env_file: Path) -> None:
    """Wczytaj plik ``.env`` do ``os.environ`` (``setdefault`` — realne env wygrywa)."""
    data = env_file.read_bytes()
    try:
        if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
            text = data.decode("utf-16")
        else:
            text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SystemExit(
            f"Nie udało się odczytać {env_file.name} — zapisz jako UTF-8: {exc}"
        ) from exc
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_dotenv() -> None:
    """Znajdź repo-lokalny ``.env`` (korzeń z ``pyproject.toml``) i wczytaj go, jeśli istnieje.

    Brak pliku nie jest błędem — operator może wyeksportować zmienne w powłoce.
    """
    here = Path(__file__).resolve()
    root = next((p for p in (here, *here.parents) if (p / "pyproject.toml").is_file()), None)
    if root is not None and (root / ".env").is_file():
        apply_env_file(root / ".env")
