"""Logika domenowa notatek — reużywalne operacje na modelach ``Note``/``NoteMetadata``.

Rozdzielone od ``models.py``, bo to LOGIKA domenowa (filtrowanie, składanie metadanych),
a nie kształt danych — jak ``core/domain/paths.py``. Bez I/O i bez zależności aplikacyjnych;
kilku konsumentów (serwisy, katalog narzędzi, przypadek „notatka ze spotkania") współdzieli
te helpery zamiast duplikować predykat filtra i konstrukcję metadanych.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sufler.core.domain.models import NoteMetadata

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date

    from sufler.core.domain.models import Note


def notes_of_project(notes: Iterable[Note], project_key: str) -> list[Note]:
    """Zwróć notatki należące do projektu o danym kluczu (bez rozróżniania wielkości liter).

    Wywołujący decyduje, czy w ogóle filtrować (pusty klucz ma pomijać filtr) — tu klucz
    jest już znany i niepusty; normalizacja wielkości liter dzieje się wewnątrz.
    """
    key = project_key.lower()
    return [n for n in notes if n.metadata.project.lower() == key]


def build_note_metadata(
    *,
    title: str,
    project: str,
    date: date,
    participants: list[str] | None = None,
    decisions: list[str] | None = None,
    action_items: list[str] | None = None,
    open_questions: list[str] | None = None,
    tags: list[str] | None = None,
) -> NoteMetadata:
    """Złóż ``NoteMetadata`` (ZAMROŻONY schemat, ADR 0003) z pól, normalizując listy do ``[]``.

    Jedno miejsce konstrukcji dla obu konsumentów ``NotesWriteService`` (narzędzie ``save_note``
    i przypadek „notatka ze spotkania"). ``None`` → ``[]``; przekazane listy zostają nietknięte.
    """
    return NoteMetadata(
        title=title,
        project=project,
        date=date,
        participants=participants or [],
        decisions=decisions or [],
        action_items=action_items or [],
        open_questions=open_questions or [],
        tags=tags or [],
    )
