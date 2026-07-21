"""Źródło godzin z pliku JSON (ADR 0035) — ATRAPA do czasu poznania realnego systemu.

Docelowe źródło (RCP, eksport kadrowy, API) nie jest jeszcze ustalone, ale kształt wiersza już
tak: osoba + dzień + zgłoszenie + minuty. Ten adapter pozwala uruchomić i przetestować CAŁĄ resztę
łańcucha — projekcję arkusza, wysyłkę, harmonogram — na danych, które sami kontrolujemy.

Podmiana na realne źródło to nowa klasa spełniająca ``HoursSource`` i jedna linia w wiringu drzwi;
nic w rdzeniu się nie zmieni.

Format pliku — lista obiektów, godziny ALBO minuty (minuty mają pierwszeństwo)::

    [
      {"source_id": "EMP-042", "day": "2026-07-15", "issue_key": "WT-12", "hours": 3.5},
      {"source_id": "EMP-017", "day": "2026-07-15", "issue_key": "WT-99", "minutes": 90,
       "comment": "przegląd kodu"}
    ]
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from workmate.core.domain.timesheet import TimesheetError, WorkEntry

logger = logging.getLogger(__name__)


class JsonHoursSource:
    """Czyta wpisy czasu z pliku JSON i filtruje je do żądanego okna."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def read(self, since: date, until: date) -> list[WorkEntry]:
        """Wpisy z okna PÓŁOTWARTEGO ``[since, until)`` dla wszystkich osób.

        Rozbicia na osoby NIE robimy tutaj — to zadanie rdzenia. Adapter, który filtruje po
        osobie, musiałby powtórzyć izolację tożsamości, a to kontrola bezpieczeństwa i ma być
        JEDNA (ADR 0035).
        """
        raw = self._load()
        entries = [self._as_entry(item, index) for index, item in enumerate(raw)]
        in_window = [entry for entry in entries if since <= entry.day < until]
        logger.info(
            "Źródło godzin %s: %d wpisów, %d w oknie %s..%s.",
            self._path,
            len(entries),
            len(in_window),
            since,
            until,
        )
        return in_window

    def _load(self) -> list[dict[str, Any]]:
        """Wczytaj plik; brak/uszkodzenie to TWARDY błąd, nie ciche zero wpisów.

        Cicha pustka wyglądałaby dokładnie jak „nikt nie pracował" — czyli tydzień bez żadnej
        wiadomości i bez śladu, że coś poszło nie tak.
        """
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise TimesheetError(f"brak pliku ze źródłem godzin: {self._path}") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise TimesheetError(f"plik ze źródłem godzin jest uszkodzony: {self._path}") from exc
        if not isinstance(data, list):
            raise TimesheetError(f"plik ze źródłem godzin musi zawierać LISTĘ wpisów: {self._path}")
        return [item for item in data if isinstance(item, dict)]

    def _as_entry(self, item: dict[str, Any], index: int) -> WorkEntry:
        """Zmapuj jeden obiekt JSON na ``WorkEntry``; brak wymaganego pola = twardy błąd."""
        try:
            day = date.fromisoformat(str(item["day"]))
            return WorkEntry(
                source_id=str(item["source_id"]),
                day=day,
                issue_key=str(item["issue_key"]).strip().upper(),
                minutes=_minutes(item),
                comment=str(item.get("comment") or ""),
            )
        except KeyError as exc:
            raise TimesheetError(
                f"wpis #{index} w {self._path} nie ma wymaganego pola {exc}"
            ) from exc
        except ValueError as exc:
            raise TimesheetError(f"wpis #{index} w {self._path} jest niepoprawny: {exc}") from exc


def _minutes(item: dict[str, Any]) -> int:
    """Minuty z pola ``minutes`` albo z ``hours`` (zaokrąglone). Minuty mają pierwszeństwo.

    Dwa pola, bo ludzie i systemy myślą różnymi jednostkami — a domena liczy wyłącznie
    w minutach, żeby sumy nie dryfowały.
    """
    if "minutes" in item:
        return int(item["minutes"])
    if "hours" in item:
        return round(float(item["hours"]) * 60)
    raise KeyError("'minutes' albo 'hours'")
