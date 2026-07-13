"""Przypadki użycia katalogu roboczego agenta (ADR 0018): odczyt i tworzenie plików.

``WorkspaceService`` (odczyt: list/read) i ``WorkspaceWriteService`` (create-only) zależą wyłącznie
od portów, więc reguła ``core ↛ adapters`` zostaje. Zdolność jest bramkowana per drzwi
(``enable_workspace``), a scope (podkatalog rozmowy) pochodzi z ZAUFANEGO id rozmowy — nigdy od
modelu. Zapis egzekwuje granice bezpieczeństwa zapisu z niezaufanych drzwi: guard znaków
sterujących, biała lista rozszerzeń, kwoty (rozmiar pliku / liczba / łączny rozmiar per rozmowa),
create-only z sufiksem przy kolizji (jak ``NotesWriteService._unique_id``).
"""
from __future__ import annotations

from dataclasses import dataclass

from workmate.core.domain.sanitize import reject_dangerous_content
from workmate.core.domain.workspace import (
    WorkspaceFile,
    WorkspaceScope,
    relpath_in_scope,
    safe_filename,
)
from workmate.core.errors import WriteError
from workmate.core.ports.workspace import WorkspaceRepository, WorkspaceWriter


@dataclass(frozen=True)
class WorkspaceLimits:
    """Granice tworzenia plików roboczych (anty-DoS z niezaufanych drzwi)."""

    max_file_bytes: int  # pojedynczy plik
    max_files_per_scope: int  # liczba plików na rozmowę
    max_total_bytes: int  # łączny rozmiar plików na rozmowę
    allowed_ext: frozenset[str]  # biała lista rozszerzeń (tekstowe)


class WorkspaceService:
    """Odczyt katalogu roboczego rozmowy: lista plików i treść pojedynczego pliku."""

    def __init__(self, repo: WorkspaceRepository) -> None:
        self._repo = repo

    def list_files(self, scope: WorkspaceScope) -> list[WorkspaceFile]:
        return self._repo.list(str(scope.dirpath()))

    def read_file(self, scope: WorkspaceScope, name: str) -> str | None:
        """Zwróć treść pliku ``name`` w katalogu rozmowy albo ``None``.

        ``name`` pochodzi od modelu — odrzucamy separatory/``..`` (obrona w głąb; adapter i tak
        pilnuje ``resolve().relative_to``), żeby odczyt nie wyszedł poza katalog rozmowy.
        """
        if "/" in name or "\\" in name or ".." in name:
            raise WriteError(f"niedozwolona nazwa pliku do odczytu: {name!r}")
        return self._repo.read(str(scope.dirpath()), name)


class WorkspaceWriteService:
    """Tworzenie plików roboczych (create-only, ADR 0018) z pełnym pakietem bezpieczeństwa."""

    def __init__(
        self, writer: WorkspaceWriter, repo: WorkspaceRepository, limits: WorkspaceLimits
    ) -> None:
        self._writer = writer
        self._repo = repo
        self._limits = limits

    def create_file(self, scope: WorkspaceScope, name: str, content: str) -> WorkspaceFile:
        """Utwórz plik w katalogu rozmowy; nigdy nie nadpisuje (kolizja → sufiks ``-2``)."""
        reject_dangerous_content(content)
        filename = safe_filename(name, allowed_ext=self._limits.allowed_ext)
        size = len(content.encode("utf-8"))
        if size > self._limits.max_file_bytes:
            raise WriteError(
                f"plik przekracza limit rozmiaru ({self._limits.max_file_bytes} B): {filename}"
            )
        existing = self._repo.list(str(scope.dirpath()))
        if len(existing) >= self._limits.max_files_per_scope:
            raise WriteError(
                f"osiągnięto limit liczby plików w rozmowie ({self._limits.max_files_per_scope})."
            )
        if sum(f.size for f in existing) + size > self._limits.max_total_bytes:
            raise WriteError(
                f"przekroczony łączny limit plików rozmowy ({self._limits.max_total_bytes} B)."
            )
        relpath = self._unique_relpath(scope, filename)
        return self._writer.create(relpath, content)

    def _unique_relpath(self, scope: WorkspaceScope, filename: str) -> str:
        """Zwróć ścieżkę pliku; przy kolizji dołóż sufiks przed rozszerzeniem (``-2``, ``-3``…)."""
        relpath = relpath_in_scope(scope, filename)
        if not self._writer.exists(relpath):
            return relpath
        stem, _, ext = filename.rpartition(".")
        suffix = 2
        while True:
            candidate = relpath_in_scope(scope, f"{stem}-{suffix}.{ext}")
            if not self._writer.exists(candidate):
                return candidate
            suffix += 1
