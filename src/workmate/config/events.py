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

    def validate(self, *, data_dir: Path | None) -> None:
        """Twardy błąd startu, gdy magazyn zdarzeń wskazuje katalog albo wnętrze bazy wiedzy.

        Ten sam inwariant, który ``WorkspaceSettings`` egzekwuje dla brudnopisu: zdarzenia
        przychodzą z drzwi i są treścią NIEZAUFANĄ, więc plik nie może wylądować w ``data/``,
        które rdzeń indeksuje jako notatki.

        ``data_dir`` wolno podać jako ``None`` — klasa bywa czytana bez pełnych ustawień rdzenia
        i wtedy zostaje sama kontrola kształtu ścieżki — ale NIE wolno go POMINĄĆ. Argument bez
        wartości domyślnej jest tu bramką w typach: drzwi GitHub, czyli jedyny proces, który te
        zdarzenia faktycznie ZAPISUJE, wołały ``validate()`` bez niczego i cały inwariant leciał
        w próżnię. Bramka stojąca po jednej stronie wspólnego pliku nie broni niczego, a pominięcie
        wyglądało dokładnie tak samo jak świadome ``None``.

        Zapisywalność sprawdza ``require_writable`` po stronie drzwi — tu nie ma efektów ubocznych.
        """
        resolved = self.db_path.resolve()
        if not resolved.name:
            raise ValueError(f"WORKMATE_EVENTS_DB musi wskazywać PLIK, jest: {resolved}.")
        if resolved.is_dir():
            raise ValueError(
                f"WORKMATE_EVENTS_DB wskazuje katalog, a ma być plikiem bazy: {resolved}."
            )
        if data_dir is not None:
            resolved_data = data_dir.resolve()
            if resolved == resolved_data or resolved_data in resolved.parents:
                raise ValueError(
                    f"WORKMATE_EVENTS_DB ({resolved}) leży w katalogu danych ({resolved_data}) "
                    "— zdarzenia z drzwi to treść niezaufana i nie mogą trafić do bazy wiedzy."
                )
