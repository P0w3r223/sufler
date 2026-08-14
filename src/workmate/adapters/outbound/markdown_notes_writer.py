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

import contextlib
import os
import uuid
from pathlib import Path

import yaml

from workmate.core.domain.models import Note, NoteMetadata
from workmate.core.errors import NoteExistsError, WriteError

_FRONTMATTER_FENCE = "---"


class MarkdownNotesWriter:
    """Zapisuje notatki do drzewa ``notes_dir/<firma>/<projekt>/<plik>.md``."""

    def __init__(self, notes_dir: Path) -> None:
        self._notes_dir = notes_dir

    def exists(self, note_id: str) -> bool:
        return _resolve_within(self._notes_dir, f"{note_id}.md").is_file()

    def write(self, note: Note) -> None:
        path = _resolve_within(self._notes_dir, f"{note.id}.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_create(path, _render(note.metadata, note.body))

    def overwrite(self, note: Note) -> None:
        """Podmień treść ISTNIEJĄCEJ notatki atomowo (ADR 0065) — nigdy w miejscu.

        ``os.replace`` na w pełni zapisanym pliku tymczasowym: czytelnik widzi albo starą, albo
        nową treść, nigdy połowy. Zapis „w miejscu" (truncate + write) zostawiałby przy awarii
        w połowie notatkę uciętą — czyli cichą utratę wiedzy pod pozorem udanej edycji.

        Odmawiamy, gdy notatki NIE MA: ``overwrite`` ma zmieniać, nie tworzyć. Gdyby tworzył,
        literówka w identyfikatorze rodziłaby po cichu nowy plik obok tego, który miał być
        poprawiony.
        """
        path = _resolve_within(self._notes_dir, f"{note.id}.md")
        if not path.is_file():
            raise WriteError(f"notatka nie istnieje, nie ma czego podmienić: {note.id}")
        _atomic_replace(path, _render(note.metadata, note.body))

    def delete(self, note_id: str) -> None:
        """Usuń POJEDYNCZY plik notatki (ADR 0065). Katalogów nie ruszamy — nawet pustych."""
        path = _resolve_within(self._notes_dir, f"{note_id}.md")
        if not path.is_file():
            raise WriteError(f"notatka nie istnieje: {note_id}")
        try:
            path.unlink()
        except OSError as exc:
            raise WriteError(f"nie udało się usunąć notatki {note_id}: {exc}") from exc


def _render(metadata: NoteMetadata, body: str) -> str:
    """Złóż plik notatki: frontmatter YAML + treść, spójnie z formatem odczytu."""
    front = yaml.safe_dump(
        metadata.model_dump(mode="python"),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    return f"{_FRONTMATTER_FENCE}\n{front}{_FRONTMATTER_FENCE}\n\n{body.strip()}\n"


def _resolve_within(notes_dir: Path, relpath: str) -> Path:
    """Rozwiąż ścieżkę notatki WEWNĄTRZ ``notes_dir``; ``WriteError`` przy ucieczce poza katalog.

    Obrona w głąb (wzorem ``filesystem_workspace.py``/``MarkdownNotesRepository.get``): dziś
    ``note.id`` przechodzi przez slugifikację serwisu (ADR 0006), więc to nie jest dziura — ale
    ta gwarancja stała dotąd wyłącznie na dyscyplinie wołających, a to JEDYNE miejsce w systemie,
    które PISZE do bazy wiedzy czytanej przez agenta.
    """
    candidate = (notes_dir / relpath).resolve()
    try:
        candidate.relative_to(notes_dir.resolve())
    except ValueError as exc:
        raise WriteError(f"ścieżka notatki poza katalogiem bazy wiedzy: {relpath!r}") from exc
    return candidate


def _atomic_create(path: Path, content: str) -> None:
    """Zapis atomowy i create-only.

    Pełna treść trafia do pliku tymczasowego, a ``os.link`` publikuje ją pod docelową nazwą —
    atomowo i tylko, gdy cel nie istnieje. Nazwa tymczasowego jest UNIKALNA per zapis
    (``pid`` + ``uuid``): dwa RÓWNOLEGŁE pisarze tego samego id (deterministyczny id notatki ze
    spotkania + pula wątków async, ADR 0043) NIE mogą dzielić jednego pliku tymczasowego — inaczej
    ``write_text`` jednego wątku (truncate) nadpisałby inode, do którego drugi wątek właśnie
    dolinkował opublikowaną notatkę. Kolizja finalnej nazwy (``FileExists``) → ``NoteExistsError``
    (wyróżniona, by ścieżka idempotentna zaraportowała „już złożona"); inny I/O → ``WriteError``.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.link(tmp, path)
    except FileExistsError as exc:
        raise NoteExistsError(f"notatka już istnieje: {path.name}") from exc
    except OSError as exc:
        raise WriteError(f"nie udało się zapisać notatki {path.name}: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)


def _atomic_replace(path: Path, content: str) -> None:
    """Podmiana atomowa: pełny zapis do pliku tymczasowego, potem ``os.replace``.

    Odwrotność ``_atomic_create``: tam ``os.link`` chroni PRZED nadpisaniem, tu ``os.replace``
    nadpisanie wykonuje — świadomie i w jednym kroku widocznym dla czytelnika. Plik tymczasowy
    leży w tym samym katalogu, bo ``os.replace`` jest atomowe tylko w obrębie systemu plików.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise WriteError(f"nie udało się podmienić notatki {path.name}: {exc}") from exc
    finally:
        # Sprzątanie nie może przykryć właściwego błędu (patrz ``filesystem_snapshots``).
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
