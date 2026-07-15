"""Trwały stan otwartych przypomnień (JSON) — idempotencja i dwukierunkowy obieg.

To NIE sekret (dane operacyjne), trzymane obok stanu drzwi teams_graph. `resolved` to lista
interwałów (weekday + HH:MM) ustalonego grafiku — tz-agnostyczna, odtwarzana przy zapisie.

Zapis jest atomowy (temp + os.replace), a odczyt tolerancyjny (ignoruje nieznane pola,
uszkodzony plik → pusty stan) — bo ten plik chroni przed podwójnym zapisem zmian.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# statusy obiegu
AWAITING_REPLY = "awaiting_reply"
AWAITING_CONFIRM = "awaiting_confirm"
APPLIED = "applied"
DECLINED = "declined"


@dataclass
class PendingReminder:
    member_id: str
    member_name: str
    chat_id: str
    week_start: str  # ISO date (poniedziałek przyszłego tygodnia)
    status: str
    watermark: str = ""  # createdDateTime ostatniej przetworzonej wiadomości pracownika
    proposal: list[dict[str, Any]] = field(default_factory=list)  # gotowiec z zeszłego tygodnia
    resolved: list[dict[str, Any]] = field(default_factory=list)  # grafik ustalony po odpowiedzi


_FIELDS = {f.name for f in fields(PendingReminder)}


def load_state(path: Path) -> dict[str, PendingReminder]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Uszkodzony plik stanu %s — startuję z pustym stanem", path)
        return {}
    # Ignoruj nieznane pola (odporność na dryf schematu).
    return {
        key: PendingReminder(**{k: v for k, v in value.items() if k in _FIELDS})
        for key, value in raw.items()
    }


def save_state(path: Path, state: dict[str, PendingReminder]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: asdict(value) for key, value in state.items()}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomowa podmiana — brak okna z uciętym plikiem
