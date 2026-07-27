"""Stan idempotencji drzwi self-service (ADR 0038) — które submisje już obsłużono.

Kluczem jest ID SUBMISJI (w trybie operatora ``<source_id>:<etykieta tygodnia>``, w docelowym
trybie live ``message.id`` z Graph), więc powtórne podanie tej samej submisji nie wygeneruje
i nie wyśle arkusza drugi raz — obrona przed podwójnym importem po stronie nadawcy jest słaba
(import robi człowiek), więc nie dokładamy do niej duplikatu z naszej strony.

Zapis ATOMOWY (temp + ``os.replace``) i odczyt TOLERANCYJNY — wzorzec z ``worklogi/state.py``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Ile ostatnich submisji trzymamy — plik nie ma rosnąć bez końca; pilotaż to garść osób.
_CAP = 500


def load(path: Path) -> dict[str, str]:
    """Wczytaj stan: ``{"EMP-1:2026-W29": "2026-07-24T10:00:00+02:00"}``; błąd → pusty."""
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
    return {str(key): str(value) for key, value in raw.items()}


def save(path: Path, state: dict[str, str]) -> None:
    """Zapisz stan atomowo, przycięty do ``_CAP`` najnowszych wpisów (po znaczniku czasu)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    trimmed = _prune(state)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomowa podmiana — brak okna z uciętym plikiem


def submission_key(source_id: str, week_label: str) -> str:
    """Klucz idempotencji jednej submisji: osoba + tydzień, którego dotyczy."""
    return f"{source_id}:{week_label}"


def _prune(state: dict[str, str]) -> dict[str, str]:
    """Zostaw ``_CAP`` najnowszych wpisów (po wartości = znaczniku czasu ISO)."""
    if len(state) <= _CAP:
        return state
    newest = sorted(state.items(), key=lambda kv: kv[1], reverse=True)[:_CAP]
    return dict(newest)
