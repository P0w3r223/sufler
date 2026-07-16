"""Trwały stan pollera GitHub (watermark ``since`` per zasób + kursor notifiera).

NIE sekret — to dane operacyjne (jak baza rozmów/zdarzeń), nie baza wiedzy. Domyślnie w
katalogu domowym poza repo i ``data/``. Kształt: ``poller`` (seed) i ``selection.next_since``
(watermark), oraz ``notifier`` (kursor). Ten sam JSON load/save co ``teams_graph.state``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    """Wczytaj stan z pliku JSON; brak pliku → pusty stan (poller go zainicjuje)."""
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save(path: Path, state: dict[str, Any]) -> None:
    """Zapisz stan do pliku JSON, tworząc katalog w razie potrzeby."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))
