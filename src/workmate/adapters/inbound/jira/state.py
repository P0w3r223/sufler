"""Trwały stan pollera Jira (watermark ``issues_since`` + kursor notifiera).

NIE sekret — to dane operacyjne (jak baza rozmów/zdarzeń), nie baza wiedzy. Domyślnie w
katalogu domowym poza repo i ``data/``. Kształt: ``issues_since`` (watermark JQL ``updated``)
oraz ``notify_cursor`` (kursor notifiera).

Zapis ATOMOWY i odczyt TOLERANCYJNY jak w ``github/state.py`` (ten sam kontrakt odporności na
``docker stop``/reboot, R1): ucięty plik nie kładzie startu, a re-poll jest idempotentny
(dedup ``events.db`` po stabilnym kluczu), więc watermark odbudowany od zera nie duplikuje.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load(path: Path) -> dict[str, Any]:
    """Wczytaj stan z pliku JSON; brak/uszkodzony plik → pusty stan (poller go zainicjuje)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        logger.warning("Stan %s nieczytelny (%s) — zaczynam od pustego.", path, exc)
        return {}
    if not isinstance(raw, dict):
        logger.warning("Stan %s ma zły kształt — zaczynam od pustego.", path)
        return {}
    return {str(key): value for key, value in raw.items()}


def save(path: Path, state: dict[str, Any]) -> None:
    """Zapisz stan atomowo — temp obok + ``os.replace`` (brak okna z uciętym plikiem)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomowa podmiana
