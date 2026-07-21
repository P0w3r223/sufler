"""Stan drzwi kart czasu (ADR 0035) — kto dostał arkusz w którym tygodniu.

Kluczem jest para ``(etykieta tygodnia, source_id)``, więc idempotencja jest per osoba per
tydzień: powtórny przebieg w tym samym tygodniu nikogo nie zaczepi drugi raz, a nowy tydzień
zaczyna z czystym kontem.

Zapis jest ATOMOWY (temp + ``os.replace``) — wzorzec z ``Powiadomienia_teams/state.py``, świadomie
NIE z ``github/state.py``, gdzie ``write_text`` zostawia okno na ucięty plik. Tutaj ucięty plik
znaczy „nikt nie dostał", czyli w najgorszym razie duplikat wiadomości u wszystkich.

Odczyt jest TOLERANCYJNY: uszkodzony plik daje pusty stan i ostrzeżenie, nie wywrócenie procesu.
Ryzyko jest asymetryczne w drugą stronę niż przy zapisie — martwy proces nie wyśle nic nikomu
przez cały tydzień, a nadmiarowa wiadomość jest tylko irytująca.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def load(path: Path) -> dict[str, str]:
    """Wczytaj stan: ``{"2026-W29:EMP-042": "2026-07-17T16:00:12+02:00"}``; błąd → pusty."""
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
    """Zapisz stan atomowo — najpierw plik tymczasowy obok, potem podmiana."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomowa podmiana — brak okna z uciętym plikiem


def key(week_label: str, source_id: str) -> str:
    """Klucz idempotencji jednej osoby w jednym tygodniu."""
    return f"{week_label}:{source_id}"


def prune(state: dict[str, str], *, keep_weeks: tuple[str, ...]) -> dict[str, str]:
    """Zostaw tylko wpisy z podanych tygodni — stan nie ma rosnąć w nieskończoność.

    Trzymamy kilka ostatnich tygodni zamiast jednego: nadrabianie po dłuższej przerwie musi
    widzieć, kto już dostał, a plik i tak waży kilobajty.
    """
    return {k: v for k, v in state.items() if k.split(":", 1)[0] in keep_weeks}
