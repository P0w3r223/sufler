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


def _reject_traversal(name: str) -> None:
    """Odrzuć nazwę pliku z separatorem albo ``..`` — nazwa bywa od modelu (obrona w głąb).

    Adapter i tak pilnuje ``resolve().relative_to(root)``; ta kontrola stoi piętro wyżej, żeby
    KAŻDA droga odczytu (tekst i bajty) miała ją tak samo — jedna funkcja zamiast dwóch kopii
    warunku, które przy trzeciej drodze rozjechałyby się po cichu.
    """
    if "/" in name or "\\" in name or ".." in name:
        raise WriteError(f"niedozwolona nazwa pliku do odczytu: {name!r}")


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
        _reject_traversal(name)
        return self._repo.read(str(scope.dirpath()), name)

    def read_bytes(self, scope: WorkspaceScope, name: str) -> bytes | None:
        """Zwróć SUROWE bajty pliku rozmowy albo ``None`` (ADR 0064 — materializacja).

        Ta sama kontrola nazwy co ``read_file``: nazwa przychodzi od modelu.
        """
        _reject_traversal(name)
        return self._repo.read_bytes(str(scope.dirpath()), name)


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

    def stage_attachment(
        self, scope: WorkspaceScope, name: str, data: bytes, *, allowed_ext: frozenset[str]
    ) -> WorkspaceFile:
        """Odłóż załącznik użytkownika na dysk katalogu rozmowy (ADR 0064).

        Osobno od ``create_file``, bo to INNA czynność z innymi regułami: treść nie pochodzi od
        modelu (więc ``reject_dangerous_content`` nad bajtami binarnymi nie ma sensu — PDF czy
        JPEG z natury zawiera bajty sterujące), a biała lista rozszerzeń jest szersza niż lista
        formatów, które model wolno mu TWORZYĆ. Wspólne zostaje to, co pilnuje dysku: limity
        rozmiaru/liczby/sumy per rozmowa i unikalna nazwa (nigdy nadpisania).

        Po co w ogóle: dotąd załącznik żył wyłącznie w blokach rozmowy, czyli na wolumenie stanu,
        którego wykonawca świadomie nie montuje (ADR 0057). Plik na dysku rozmowy jest jedyną
        formą, którą widzi ZARAZEM powłoka (`workmate-extract`) i ``File(read)`` — i jedyną, która
        przeżywa kompaktowanie kontekstu (ADR 0014), po którym załącznik zostaje samym opisem.
        """
        filename = safe_filename(name, allowed_ext=allowed_ext)
        size = len(data)
        if size > self._limits.max_file_bytes:
            raise WriteError(
                f"załącznik przekracza limit rozmiaru ({self._limits.max_file_bytes} B): {filename}"
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
        return self._writer.create_bytes(relpath, data)

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
