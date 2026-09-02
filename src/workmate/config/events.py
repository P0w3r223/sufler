"""Ustawienia magazynu zdarzeń mostu (SQLite ``events.db``, append-only)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from workmate.config._env import _path_from_env

# Domyślny wspólny magazyn zdarzeń (EventStore, ADR 0019): OSOBNY plik od conversations.db
# (tamten robi rebuild tabeli przy migracji FK), POZA repo i data/ — to dane operacyjne
# (warstwa spajająca drzwi), nie baza wiedzy. Nadpisywalny przez WORKMATE_EVENTS_DB.
_DEFAULT_EVENTS_DB = Path.home() / ".workmate" / "events.db"


@dataclass(frozen=True)
class EventsSettings:
    """Konfiguracja wspólnego magazynu zdarzeń (EventStore, ADR 0019) — warstwa spajająca drzwi.

    Baza SQLite leży POZA ``data/`` (folder indeksowany przez rdzeń) i poza repo — to dane
    operacyjne (zdarzenia z drzwi), nie baza wiedzy; poisoned zdarzenie nie może trafić do
    notatek, które agent czyta. Osobny plik od ``conversations.db`` (patrz ``_DEFAULT_EVENTS_DB``).
    """

    db_path: Path = _DEFAULT_EVENTS_DB

    @classmethod
    def from_env(cls) -> EventsSettings:
        return cls(db_path=_path_from_env("WORKMATE_EVENTS_DB", _DEFAULT_EVENTS_DB))
