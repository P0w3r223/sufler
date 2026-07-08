"""Repozytorium notatek oparte na plikach Markdown z frontmatter YAML.

Format pliku (patrz ``docs/reference/note-schema.md``)::

    ---
    title: ...
    project: mpwik
    date: 2025-06-12
    participants: [...]
    decisions: [...]
    action_items: [...]
    open_questions: [...]
    tags: [...]
    ---

    Treść notatki w Markdown...

Identyfikator notatki to jej ścieżka względem katalogu notatek, bez rozszerzenia
(np. ``mpwik/scada-integration/2025-06-12-przeglad-integracji``). Notatki są naszymi kontrolowanymi
danymi, więc błędny plik = błąd danych: podnosimy ``NoteParseError`` z kontekstem
(fail fast), a granica MCP zamienia go na czytelny komunikat.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from workmate.core.domain.models import Note, NoteMetadata
from workmate.core.errors import RepositoryError

_FRONTMATTER_FENCE = "---"


class NoteParseError(RepositoryError):
    """Notatka ma niepoprawny frontmatter albo nie da się jej sparsować."""


class MarkdownNotesRepository:
    """Ładuje notatki z drzewa katalogów ``notes_dir/<firma>/<projekt>/<plik>.md``."""

    def __init__(self, notes_dir: Path) -> None:
        self._notes_dir = notes_dir

    def all(self) -> list[Note]:
        if not self._notes_dir.is_dir():
            raise RepositoryError(f"Katalog notatek nie istnieje: {self._notes_dir}")
        return [self._load(path) for path in sorted(self._notes_dir.rglob("*.md"))]

    def get(self, note_id: str) -> Note | None:
        path = self._notes_dir / f"{note_id}.md"
        # Identyfikator pochodzi od wołającego: upewnij się, że złożona ścieżka
        # nie wychodzi poza katalog notatek (ochrona przed path traversal, np.
        # note_id="../../../../etc/hosts").
        try:
            path.resolve().relative_to(self._notes_dir.resolve())
        except ValueError:
            return None
        if not path.is_file():
            return None
        return self._load(path)

    def _load(self, path: Path) -> Note:
        raw = path.read_text(encoding="utf-8")
        # Ścieżka WZGLĘDNA wobec katalogu notatek w komunikatach błędów: nie ujawnia
        # bezwzględnej struktury serwera (drzwi HTTP) i jest przenośna między maszynami.
        location = path.relative_to(self._notes_dir).as_posix()
        meta_dict, body = _split_frontmatter(raw, location)
        try:
            metadata = NoteMetadata.model_validate(meta_dict)
        except ValidationError as exc:
            raise NoteParseError(
                f"{location}: nieprawidłowy frontmatter notatki: {exc}"
            ) from exc
        note_id = path.relative_to(self._notes_dir).with_suffix("").as_posix()
        return Note(id=note_id, metadata=metadata, body=body.strip())


def _split_frontmatter(raw: str, location: str) -> tuple[dict[str, Any], str]:
    """Rozdziel plik na słownik frontmatter (YAML) i treść (Markdown).

    ``location`` to ścieżka względna notatki — trafia do komunikatów błędów, więc
    musi być bezpieczna do pokazania klientowi (bez bezwzględnej ścieżki serwera).
    """
    if not raw.lstrip().startswith(_FRONTMATTER_FENCE):
        raise NoteParseError(
            f"{location}: brak bloku frontmatter ('---') na początku pliku"
        )

    # maxsplit=2: dzielimy tylko na pierwszych dwóch '---', ewentualne '---'
    # w treści (linie poziome) zostają nietknięte w części z treścią.
    _, _, remainder = raw.partition(_FRONTMATTER_FENCE)
    front, fence, body = remainder.partition(f"\n{_FRONTMATTER_FENCE}")
    if not fence:
        raise NoteParseError(f"{location}: brak zamykającego '---' frontmatter")

    try:
        meta = yaml.safe_load(front)
    except yaml.YAMLError as exc:
        raise NoteParseError(
            f"{location}: błąd składni YAML we frontmatter: {exc}"
        ) from exc

    if not isinstance(meta, dict):
        raise NoteParseError(f"{location}: frontmatter musi być mapą klucz-wartość")
    return meta, body
