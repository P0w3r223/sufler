"""Trwały stan pollera Teams (watermark wątków + dedup ``replied``).

NIE sekret — to dane operacyjne (jak baza rozmów), nie baza wiedzy. Domyślnie w
katalogu domowym poza repo i ``data/``. Kształt: patrz ``poller`` (seed) i
``selection.plan_channel`` (przejścia).

Zapis jest ATOMOWY (temp + ``os.replace``) — wzorzec z ``github/state.py``: ``docker stop`` w
trakcie ``write_text`` zostawiał ucięty JSON, który przy starcie kładł ``load()`` = crash-loop
(R1). Odczyt jest TOLERANCYJNY: uszkodzony plik daje pusty stan i ostrzeżenie, nie wywrócenie
procesu — re-poll jest idempotentny (dedup po stabilnym kluczu w ``replied``), więc watermark
odbudowany od zera nie duplikuje odpowiedzi.
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
    return raw


def save(path: Path, state: dict[str, Any]) -> None:
    """Zapisz stan atomowo — temp obok + ``os.replace`` (brak okna z uciętym plikiem)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomowa podmiana
