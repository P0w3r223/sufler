"""Wspólne atrapy, dane i IZOLACJA ŚRODOWISKA dla testów.

Atrapy implementują porty (``NotesRepository`` / ``ProjectsRepository``)
strukturalnie — bez dziedziczenia — dzięki czemu serwisy testujemy w pełni
w pamięci, bez dotykania dysku.

Izolacja środowiska (``_srodowisko_bez_konfiguracji_maszyny``) jest tu, bo wynik pakietu nie
może zależeć od maszyny. ``config.py`` czyta WYŁĄCZNIE ``os.environ`` (``.env`` wczytują dopiero
wejścia drzwi przez ``env.load_dotenv``), więc pod pytestem plik ``.env`` z repo nie działa —
ale realna powłoka operatora działa. Empirycznie: z ``WORKMATE_GITHUB_ENABLE_CI_AUTO_COMMENT=true``
w środowisku ``test_enable_ci_auto_comment_defaults_false`` przewracał się na maszynie, na której
nikt nie tknął kodu. Testy, które chcą zmiennej, ustawiają ją same przez ``monkeypatch``.
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from workmate.core.domain.models import (
    Note,
    NoteMetadata,
    Project,
    ProjectStatusRecord,
)

# Zmienne spoza przestrzeni ``WORKMATE_*``, które i tak sterują naszym kodem: klucz SDK czytany
# awaryjnie przez ``AgentSettings.from_env`` (obecny na maszynie dewelopera) oraz zmienne, przez
# które SDK/biblioteki wychodzą do sieci — pakiet ma biegać bez sieci, także gdy ktoś je ustawił.
_OBCE_ZMIENNE = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
)


@pytest.fixture(autouse=True)
def _srodowisko_bez_konfiguracji_maszyny(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zdejmij KAŻDĄ zmienną ``WORKMATE_*`` (i klucze SDK) na czas testu.

    ``monkeypatch`` przywraca stan po teście, więc uruchomienie pakietu nie zmienia środowiska
    powłoki. Fixture jest ``autouse`` i funkcyjny: biegnie PRZED ciałem testu, a ustawienia
    robione w teście (``monkeypatch.setenv``) mają pierwszeństwo, bo są późniejsze.
    """
    for name in list(os.environ):
        if name.startswith("WORKMATE_"):
            monkeypatch.delenv(name, raising=False)
    for name in _OBCE_ZMIENNE:
        monkeypatch.delenv(name, raising=False)


def make_note(
    note_id: str,
    *,
    project: str,
    title: str,
    on: date,
    participants: list[str] | None = None,
    body: str = "",
    action_items: list[str] | None = None,
    tags: list[str] | None = None,
) -> Note:
    return Note(
        id=note_id,
        metadata=NoteMetadata(
            title=title,
            project=project,
            date=on,
            participants=participants or [],
            action_items=action_items or [],
            tags=tags or [],
        ),
        body=body,
    )


class FakeNotesRepository:
    """Atrapa ``NotesRepository`` trzymająca notatki w liście."""

    def __init__(self, notes: list[Note]) -> None:
        self._notes = notes

    def all(self) -> list[Note]:
        return list(self._notes)

    def get(self, note_id: str) -> Note | None:
        return next((n for n in self._notes if n.id == note_id), None)


class FakeProjectsRepository:
    """Atrapa ``ProjectsRepository`` trzymająca projekty i statusy w mapach."""

    def __init__(
        self,
        projects: list[Project],
        records: dict[str, ProjectStatusRecord],
    ) -> None:
        self._projects = projects
        self._records = records

    def all(self) -> list[Project]:
        return list(self._projects)

    def get(self, key: str) -> Project | None:
        return next((p for p in self._projects if p.key == key), None)

    def status_record(self, key: str) -> ProjectStatusRecord | None:
        return self._records.get(key)


class FakeNotesWriter:
    """Atrapa ``NotesWriter`` trzymająca zapisane notatki w mapie id → Note."""

    def __init__(self) -> None:
        self.saved: dict[str, Note] = {}

    def exists(self, note_id: str) -> bool:
        return note_id in self.saved

    def write(self, note: Note) -> None:
        self.saved[note.id] = note


@pytest.fixture
def sample_notes() -> list[Note]:
    return [
        make_note(
            "mpwik/scada-integration/2025-06-12-api",
            project="scada-integration",
            title="Przegląd kontraktu API",
            on=date(2025, 6, 12),
            participants=["Anna Kowalska", "Marek Nowak"],
            body="Domknęliśmy kontrakt API oparty o wąskie endpointy.",
            action_items=["Wdrożyć walidację wejścia", "Przygotować checklistę"],
            tags=["api", "bezpieczenstwo"],
        ),
        make_note(
            "mpwik/scada-integration/2025-05-14-kickoff",
            project="scada-integration",
            title="Kickoff integracji",
            on=date(2025, 5, 14),
            participants=["Anna Kowalska"],
            body="Zakres MVP to odczyt danych ze SCADA przez API.",
            action_items=["Szkic API"],
            tags=["kickoff", "api"],
        ),
        make_note(
            "biap/workmate/2025-06-10-schemat",
            project="workmate",
            title="Schemat notatki",
            on=date(2025, 6, 10),
            participants=["Piotr Zieliński"],
            body="Zablokowaliśmy schemat notatki i kontrakt narzędzi.",
            action_items=[],
            tags=["schemat"],
        ),
    ]


# --- Refleksja po konfiguracji -------------------------------------------------------------
# Bramki refleksyjne (ścieżki stanu w ``test_config``, bramki domyślnie zamknięte w
# ``test_gates_closed_by_default``, nośniki sekretu w ``security/test_secret_leakage``) oglądały
# ``vars(config)``, gdy konfiguracja była JEDNYM plikiem. Po rozbiciu na pakiet ``vars`` widzi
# tylko re-eksport z ``__init__`` — a stała pominięta w re-eksporcie wymykałaby się bramce BEZ
# ŚLADU (test przechodzi na pustym zbiorze). Dlatego refleksja chodzi po WSZYSTKICH modułach
# pakietu: nowy moduł domeny jest objęty bramkami z automatu, bez dopisywania go gdziekolwiek.


def przestrzen_config() -> dict[str, object]:
    """Nazwy najwyższego poziomu CAŁEGO pakietu ``workmate.config`` — z każdego modułu domeny."""
    import importlib
    import pkgutil

    from workmate import config

    przestrzen: dict[str, object] = dict(vars(config))
    for info in pkgutil.iter_modules(config.__path__):
        modul = importlib.import_module(f"{config.__name__}.{info.name}")
        przestrzen.update(vars(modul))
    return przestrzen


def pochodzi_z_config(obj: object) -> bool:
    """Czy obiekt jest ZDEFINIOWANY w ``workmate.config`` (a nie tylko tam zaimportowany)."""
    modul = getattr(obj, "__module__", "")
    return modul == "workmate.config" or modul.startswith("workmate.config.")
