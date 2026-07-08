"""Modele domenowe WorkMate.

Schemat notatki (``NoteMetadata``) jest kontraktem danych z Bramki 1 roadmapy
i musi być stały: to on czyni odpytywanie i śledzenie stanu prostym
("struktura przy zapisie, nie przy odczycie"). Zmiana pól = ADR + Bramka 1.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class NoteMetadata(BaseModel):
    """Ustrukturyzowany nagłówek (frontmatter) notatki ze spotkania.

    Odpowiada polom z roadmapy: projekt, data, uczestnicy, decyzje,
    action items, otwarte pytania (+ tytuł i tagi ułatwiające wyszukiwanie).
    """

    title: str
    project: str
    date: date
    participants: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class Note(BaseModel):
    """Pełna notatka: identyfikator, metadane i treść (markdown poniżej frontmatter)."""

    id: str
    metadata: NoteMetadata
    body: str


class NoteSummary(BaseModel):
    """Lekki wynik wyszukiwania — metadane + fragment, bez pełnej treści."""

    id: str
    title: str
    project: str
    date: date
    participants: list[str]
    snippet: str
    score: int


class Project(BaseModel):
    """Pozycja w rejestrze projektów pionu.

    ``company`` to klucz firmy/klienta, do której należy projekt (ADR 0005).
    Jest jedynym źródłem prawdy o firmie — notatka go nie duplikuje.
    """

    key: str
    company: str
    name: str
    description: str


class ProjectStatusRecord(BaseModel):
    """Zadeklarowany status projektu — źródło prawdy: rejestr (registry.yaml)."""

    key: str
    status: str
    health: str
    phase: str
    summary: str
    last_updated: date


class ProjectStatus(BaseModel):
    """Status projektu zwracany przez rdzeń.

    Łączy część *zadeklarowaną* (z rejestru) z częścią *syntetyzowaną* z notatek
    (liczba notatek, data ostatniej, liczba otwartych action items). Rdzeń nie
    zastępuje źródeł prawdy — on je syntetyzuje.
    """

    key: str
    company: str
    name: str
    status: str
    health: str
    phase: str
    summary: str
    last_updated: date
    # Pola wyliczane z notatek (synteza, nie źródło prawdy):
    notes_count: int
    latest_note_date: date | None
    open_action_items: int
