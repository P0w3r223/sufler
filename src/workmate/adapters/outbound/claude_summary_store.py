"""Odczyt zebranych wyników ``claude_summary`` z katalogu (ADR 0036) — port ``TaskSummarySource``.

Store to katalog plików JSON w kontrakcie ``claude_summary``: pole ``person`` = e-mail git,
``days[]`` z ``date`` oraz ``llm_prose``/``prompts``/``commits``. Indeksujemy po ``person`` małymi
literami — spójnie z kanonizacją ``git_email`` w katalogu tożsamości. MECHANIZM ZBIERANIA plików
z maszyn użytkowników jest ODŁOŻONY (S6); na teraz operator wrzuca pliki do katalogu ręcznie.

Brak katalogu/pliku → brak komentarzy (DEGRADACJA, nie błąd): godziny i klucze issue pozostają
kompletne, pusta jest tylko kolumna komentarza. Treść to DANE (już zredagowane przez
``claude_summary``) — wyciągamy wyłącznie stringi, a projekcja arkusza sanityzuje znaki sterujące
i blokuje formuły.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from workmate.core.domain.day_comment import build_comment

logger = logging.getLogger(__name__)


class ClaudeSummaryStore:
    """``TaskSummarySource`` czytający katalog wyników ``claude_summary`` (indeks budowany raz)."""

    def __init__(self, store_dir: Path) -> None:
        self._store_dir = store_dir
        self._index: dict[str, dict[date, str]] | None = None

    def comments_by_day(self, git_email: str, since: date, until: date) -> dict[date, str]:
        person_days = self._load_index().get(git_email.strip().lower(), {})
        return {day: comment for day, comment in person_days.items() if since <= day < until}

    def _load_index(self) -> dict[str, dict[date, str]]:
        if self._index is None:
            self._index = self._build_index()
        return self._index

    def _build_index(self) -> dict[str, dict[date, str]]:
        index: dict[str, dict[date, str]] = {}
        if not self._store_dir.is_dir():
            logger.info("Brak katalogu claude_summary (%s) — komentarze puste.", self._store_dir)
            return index
        for path in sorted(self._store_dir.glob("*.json")):
            report = _read_report(path)
            if report is None:
                continue
            person = str(report.get("person") or "").strip().lower()
            days = report.get("days")
            if not person or not isinstance(days, list):
                continue
            bucket = index.setdefault(person, {})
            for day_data in days:
                if not isinstance(day_data, dict):
                    continue
                day = _parse_date(day_data.get("date"))
                if day is None:
                    continue
                comment = build_comment(
                    llm_prose=_as_str_or_none(day_data.get("llm_prose")),
                    commit_messages=_strings(day_data.get("commits"), "message"),
                    prompt_texts=_strings(day_data.get("prompts"), "text"),
                )
                if comment:
                    bucket[day] = comment
        return index


def _read_report(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("Pomijam uszkodzony plik claude_summary %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _as_str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _strings(items: Any, key: str) -> list[str]:
    """Lista wartości ``key`` (jako string) z listy słowników; odporna na dziwny kształt.

    ``item.get(key) or ""`` (nie ``get(key, "")``): domyślna wartość działa tylko przy BRAKU klucza,
    a jawny ``null`` w JSON dałby ``str(None) == "None"`` — literał, który wjechałby do komentarza.
    """
    if not isinstance(items, list):
        return []
    return [str(item.get(key) or "") for item in items if isinstance(item, dict)]
