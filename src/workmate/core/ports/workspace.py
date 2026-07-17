"""Porty katalogu roboczego agenta (ADR 0018) — read osobno od write.

Wzorzec jak ``NotesRepository``/``NotesWriter``: odczyt jest jawnie tylko-do-odczytu, a zapis
(create-only) jest wstrzykiwany osobno i tylko tym drzwiom, które mają na to pozwolenie
(bramka ``enable_workspace``, osobna od ``enable_write`` dla notatek). Ścieżki są WZGLĘDNE wobec
korzenia workspace (posix, wyliczone w domenie); adapter rozwiązuje je z ochroną path-traversal.
"""

from __future__ import annotations

from typing import Protocol

from workmate.core.domain.workspace import WorkspaceFile


class WorkspaceRepository(Protocol):
    """Dostęp *tylko do odczytu* do plików katalogu roboczego (w obrębie rozmowy)."""

    def list(self, scope_dir: str) -> list[WorkspaceFile]:
        """Zwróć pliki w katalogu rozmowy (pusta lista, gdy katalog nie istnieje)."""
        ...

    def read(self, scope_dir: str, name: str) -> str | None:
        """Zwróć treść pliku ``name`` w katalogu rozmowy albo ``None``, gdy nie istnieje."""
        ...


class WorkspaceWriter(Protocol):
    """Dostęp *do zapisu* plików roboczych (create-only, ADR 0018).

    Osobny od ``WorkspaceRepository`` — odczyt zostaje jawnie tylko-do-odczytu, a możliwość
    tworzenia plików wstrzykiwana jest tylko drzwiom z bramką ``enable_workspace``.
    """

    def exists(self, relpath: str) -> bool:
        """Czy plik pod ścieżką (względem korzenia workspace) już istnieje (kontrola kolizji)?"""
        ...

    def create(self, relpath: str, content: str) -> WorkspaceFile:
        """Utwórz plik atomowo i create-only pod ścieżką względną; zwróć jego opis."""
        ...
