"""Gdzie mieszka dziennik i ładunki, gdy operator nie wskaże katalogu.

Osobny moduł, bo to jedyne miejsce, w którym program zgaduje ścieżkę. Każde polecenie przyjmuje
`--magazyn`, więc testy i przeglądy nie muszą dotykać katalogu użytkownika — a domyślna ścieżka
jest wypisywana na ekranie przy każdym zapisie, żeby nikt nie musiał jej szukać w kodzie.
"""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_data_dir

NAZWA_APLIKACJI = "krs-tool"


def domyslny_magazyn() -> Path:
    """Katalog danych użytkownika — bez nazwy producenta w ścieżce."""
    return Path(user_data_dir(NAZWA_APLIKACJI, appauthor=False))
