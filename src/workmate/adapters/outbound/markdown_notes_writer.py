"""Zapis notatek do plików Markdown z frontmatter YAML (implementacja ``NotesWriter``).

Odwrotność ``MarkdownNotesRepository``: bierze ``Note`` i zapisuje ją pod ścieżką
``notes_dir/<note.id>.md``, tworząc brakujące katalogi firmy/projektu. Zapis jest
**atomowy i create-only**: pełna treść ląduje w pliku tymczasowym, a ``os.link``
publikuje ją pod docelową nazwą tylko, gdy jeszcze nie istnieje. Daje to dwie
gwarancje z ADR 0006 naraz: ``MarkdownNotesRepository.all`` (czytający katalog
zachłannie) widzi albo nic, albo cały plik — nigdy częściowy; a istniejąca
notatka nigdy nie zostaje nadpisana (także przy równoległych pisarzach) —
kolizja to błąd, nie ciche skasowanie.

Serializacja frontmatter idzie przez ``yaml.safe_dump`` z ``sort_keys=False``,
więc kolejność pól odpowiada modelowi (title, project, date, …), a
``allow_unicode=True`` zachowuje polskie znaki w treści metadanych.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from workmate.core.domain.models import Note, NoteMetadata
from workmate.core.errors import WriteError

_FRONTMATTER_FENCE = "---"


class MarkdownNotesWriter:
    """Zapisuje notatki do drzewa ``notes_dir/<firma>/<projekt>/<plik>.md``."""

    def __init__(self, notes_dir: Path) -> None:
        self._notes_dir = notes_dir

    def exists(self, note_id: str) -> bool:
        return (self._notes_dir / f"{note_id}.md").is_file()

    def write(self, note: Note) -> None:
        path = self._notes_dir / f"{note.id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_create(path, _render(note.metadata, note.body))


def _render(metadata: NoteMetadata, body: str) -> str:
    """Złóż plik notatki: frontmatter YAML + treść, spójnie z formatem odczytu."""
    front = yaml.safe_dump(
        metadata.model_dump(mode="python"),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    return f"{_FRONTMATTER_FENCE}\n{front}{_FRONTMATTER_FENCE}\n\n{body.strip()}\n"


def _atomic_create(path: Path, content: str) -> None:
    """Zapis atomowy i create-only.

    Pełna treść trafia do pliku tymczasowego, a ``os.link`` publikuje ją pod
    docelową nazwą — atomowo i tylko, gdy cel nie istnieje. Kolizja (``FileExists``)
    i błąd I/O (``OSError``) są opakowywane w ``WriteError``, więc granica MCP
    degraduje łagodnie (``{"error": ...}``) zamiast wywracać serwer.
    """
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.link(tmp, path)
    except FileExistsError as exc:
        raise WriteError(f"notatka już istnieje: {path.name}") from exc
    except OSError as exc:
        raise WriteError(f"nie udało się zapisać notatki {path.name}: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)
