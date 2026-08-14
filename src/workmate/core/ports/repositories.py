"""Porty magazynów danych (interfejsy strukturalne).

Użycie ``Protocol`` zamiast klas bazowych oznacza, że dowolna klasa o zgodnych
sygnaturach jest akceptowana — bez dziedziczenia. Ułatwia to testy (proste
atrapy w pamięci) i podmianę implementacji.
"""

from __future__ import annotations

from typing import Protocol

from workmate.core.domain.models import Note, Project, ProjectStatusRecord


class NotesRepository(Protocol):
    """Dostęp *tylko do odczytu* do notatek ze spotkań."""

    def all(self) -> list[Note]:
        """Zwróć wszystkie notatki (Faza 1: mały zbiór, ładowany w całości)."""
        ...

    def get(self, note_id: str) -> Note | None:
        """Zwróć notatkę po identyfikatorze albo ``None``, gdy nie istnieje."""
        ...


class NotesWriter(Protocol):
    """Dostęp *do zapisu* notatek (Bramka 2, ADR 0006).

    Świadomie osobny od ``NotesRepository`` — dzięki temu odczyt pozostaje jawnie
    tylko-do-odczytu, a możliwość zapisu jest wstrzykiwana tylko tym drzwiom,
    które mają na nią pozwolenie (profil uprawnień per drzwi).
    """

    def exists(self, note_id: str) -> bool:
        """Czy notatka o danym identyfikatorze już istnieje (kontrola kolizji)?"""
        ...

    def write(self, note: Note) -> None:
        """Zapisz notatkę atomowo pod ścieżką z ``note.id`` — CREATE-ONLY.

        Kolizja to błąd, nigdy ciche nadpisanie: na tej własności stoi idempotencja notatek
        ze spotkania i wątku (ADR 0043/0048), więc ``write`` zostaje nietknięte przez ADR 0065.
        Mutacja ma WŁASNE czasowniki niżej.
        """
        ...

    def overwrite(self, note: Note, *, expected_sha256: str) -> None:
        """Podmień treść ISTNIEJĄCEJ notatki (ADR 0065) — osobny czasownik, nie tryb ``write``.

        Osobny rozmyślnie: gdyby ``write`` dostał flagę „nadpisuj", każdy dotychczasowy wołający
        (trzy ścieżki ``save_*``, seed korpusu, atrapy w testach) niósłby domyślnie zdolność,
        której nie potrzebuje — a jedna pomyłka cicho zamieniłaby zapis idempotentny w niszczący.
        Zapis jest atomowy (nowy plik + podmiana), nigdy w miejscu.

        ``expected_sha256`` to skrót pliku z chwili ODCZYTU. Między odczytem a zapisem leży
        wywołanie sędziego — sekundy — a drzwi obsługują tury równolegle. Bez tej kontroli dwie
        równoległe zmiany tej samej notatki nadpisywały się wzajemnie i wersja pośrednia ginęła
        BEZ MIGAWKI, czyli w jedynym stanie, którego ta warstwa ma nie dopuszczać.
        """
        ...

    def digest(self, note_id: str) -> str:
        """Skrót pliku notatki — znacznik wersji do optymistycznej kontroli współbieżności.

        Wołający bierze go PRZED długą operacją (ocena zmiany) i podaje przy zapisie; różnica
        znaczy „ktoś zmienił notatkę w międzyczasie" i zapis ma się wtedy nie odbyć.
        """
        ...

    def delete(self, note_id: str) -> None:
        """Usuń POJEDYNCZĄ notatkę (ADR 0065). Nigdy katalog, nigdy wzorzec, nigdy rekurencyjnie.

        Wołający ma obowiązek zapisać migawkę PRZED wywołaniem — port jej nie robi, bo to
        decyzja polityki, a nie systemu plików.
        """
        ...


class ProjectsRepository(Protocol):
    """Dostęp *tylko do odczytu* do rejestru projektów pionu."""

    def all(self) -> list[Project]:
        """Zwróć wszystkie projekty z rejestru."""
        ...

    def get(self, key: str) -> Project | None:
        """Zwróć projekt po kluczu albo ``None``."""
        ...

    def status_record(self, key: str) -> ProjectStatusRecord | None:
        """Zwróć zadeklarowany status projektu albo ``None``."""
        ...
