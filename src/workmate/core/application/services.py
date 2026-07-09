"""Serwisy aplikacyjne — logika czterech narzędzi Fazy 1.

Świadomie proste: wyszukiwanie po metadanych i treści nad małym, dobrze
uschematyzowanym zbiorem notatek (bez RAG-a — to Faza 3). Serwisy zależą tylko
od portów (``NotesRepository`` / ``ProjectsRepository``), więc są w pełni
testowalne na atrapach w pamięci, bez dotykania dysku.
"""
from __future__ import annotations

from workmate.core.domain.models import (
    Note,
    NoteMetadata,
    NoteSummary,
    Project,
    ProjectStatus,
)
from workmate.core.domain.paths import note_id as build_note_id
from workmate.core.domain.sanitize import reject_dangerous_content
from workmate.core.errors import WriteError
from workmate.core.ports.repositories import (
    NotesRepository,
    NotesWriter,
    ProjectsRepository,
)

# Maksymalna długość fragmentu (snippet) zwracanego w wynikach wyszukiwania.
_SNIPPET_LENGTH = 200
# Tytuł waży więcej niż pozostałe pola przy ustalaniu trafności.
_TITLE_WEIGHT = 3
# Pokrycie (ile RÓŻNYCH słów zapytania w ogóle trafiło) dominuje nad ważeniem pól:
# notatka z większą liczbą słów zapytania jest trafniejsza niż taka, która wielokrotnie
# trafia jedno słowo w polu o wysokiej wadze. Stała > maks. ważenia pól dla realnych
# zapytań (suma wag pól ≈ 9 na słowo, więc bezpieczne do ~100 słów w zapytaniu).
_COVERAGE_WEIGHT = 1000


class NotesService:
    """Przypadki użycia dla notatek: wyszukiwanie i odczyt pojedynczej notatki."""

    def __init__(self, notes: NotesRepository) -> None:
        self._notes = notes

    def search_notes(
        self,
        query: str,
        *,
        project: str | None = None,
        participant: str | None = None,
        limit: int = 10,
    ) -> list[NoteSummary]:
        """Znajdź notatki pasujące do zapytania, z opcjonalnymi filtrami.

        Zapytanie jest tokenizowane na słowa (nie traktowane jako jedna fraza), więc
        „koszt integracji" trafia notatkę z oboma słowami w dowolnej kolejności.
        Dopasowanie jest po metadanych i treści (bez rozróżniania wielkości liter).
        Wyniki są sortowane malejąco po trafności, a przy remisie — po dacie.
        """
        terms = query.lower().split()
        candidates = self._filtered(project=project, participant=participant)

        summaries: list[NoteSummary] = []
        for note in candidates:
            score = _score(note, terms) if terms else 1
            if score == 0:
                continue
            summaries.append(_summarize(note, terms, score))

        summaries.sort(key=lambda s: (s.score, s.date), reverse=True)
        return summaries[: max(0, limit)]

    def get_note(self, note_id: str) -> Note | None:
        """Zwróć pełną notatkę po id ``<firma>/<projekt>/<data>-<slug>`` albo ``None``."""
        return self._notes.get(note_id)

    def _filtered(
        self, *, project: str | None, participant: str | None
    ) -> list[Note]:
        notes = self._notes.all()
        if project:
            key = project.lower()
            notes = [n for n in notes if n.metadata.project.lower() == key]
        if participant:
            needle = participant.lower()
            notes = [
                n
                for n in notes
                if any(needle in p.lower() for p in n.metadata.participants)
            ]
        return notes


class ProjectsService:
    """Przypadki użycia dla projektów: lista i status.

    ``get_project_status`` syntetyzuje zadeklarowany status (z rejestru) z faktami
    wyliczonymi z notatek — dlatego serwis potrzebuje obu repozytoriów.
    """

    def __init__(self, projects: ProjectsRepository, notes: NotesRepository) -> None:
        self._projects = projects
        self._notes = notes

    def list_projects(self) -> list[Project]:
        """Zwróć projekty pionu, posortowane po kluczu."""
        return sorted(self._projects.all(), key=lambda p: p.key)

    def get_project_status(self, key: str) -> ProjectStatus | None:
        """Zwróć status projektu albo ``None``, gdy projekt nie istnieje."""
        project = self._projects.get(key)
        record = self._projects.status_record(key)
        if project is None or record is None:
            return None

        project_notes = [
            n for n in self._notes.all() if n.metadata.project.lower() == key.lower()
        ]
        note_dates = [n.metadata.date for n in project_notes]
        open_action_items = sum(len(n.metadata.action_items) for n in project_notes)

        return ProjectStatus(
            key=record.key,
            company=project.company,
            name=project.name,
            status=record.status,
            health=record.health,
            phase=record.phase,
            summary=record.summary,
            last_updated=record.last_updated,
            notes_count=len(project_notes),
            latest_note_date=max(note_dates) if note_dates else None,
            open_action_items=open_action_items,
        )


class NotesWriteService:
    """Przypadek użycia zapisu notatki (Bramka 2, ADR 0006).

    Wylicza miejsce zapisu z metadanych (firma z rejestru + projekt + data +
    slug tytułu) i zapisuje przez port ``NotesWriter``. Nigdy nie nadpisuje
    istniejącej notatki — przy kolizji dokłada sufiks (``-2``, ``-3``, …).
    Zależy wyłącznie od portów, więc reguła zależności rdzeń↛adaptery zostaje
    zachowana.
    """

    def __init__(self, writer: NotesWriter, projects: ProjectsRepository) -> None:
        self._writer = writer
        self._projects = projects

    def save_note(self, metadata: NoteMetadata, body: str) -> Note:
        """Zapisz nową notatkę i zwróć ją z nadanym identyfikatorem."""
        # Strażnik wstrzyknięć (obrona w głąb): odrzuć NUL/znaki sterujące w polach
        # tekstowych, zanim cokolwiek trafi do pliku bazy.
        reject_dangerous_content(
            metadata.title,
            body,
            *metadata.participants,
            *metadata.decisions,
            *metadata.action_items,
            *metadata.open_questions,
            *metadata.tags,
        )
        project = self._projects.get(metadata.project)
        if project is None:
            raise WriteError(f"projekt nie istnieje w rejestrze: {metadata.project!r}")
        try:
            base_id = build_note_id(
                project.company, project.key, metadata.date, metadata.title
            )
        except ValueError as exc:
            raise WriteError(str(exc)) from exc

        note = Note(id=self._unique_id(base_id), metadata=metadata, body=body.strip())
        self._writer.write(note)
        return note

    def _unique_id(self, base_id: str) -> str:
        """Zwróć ``base_id`` lub, jeśli zajęty, z najniższym wolnym sufiksem."""
        if not self._writer.exists(base_id):
            return base_id
        suffix = 2
        while self._writer.exists(f"{base_id}-{suffix}"):
            suffix += 1
        return f"{base_id}-{suffix}"


def _score(note: Note, terms: list[str]) -> int:
    """Trafność = pokrycie (różne słowa zapytania) × waga + ważenie pól.

    Pokrycie dominuje (``_COVERAGE_WEIGHT``): notatka trafiająca więcej różnych słów
    zapytania jest wyżej niż taka, która wielokrotnie trafia jedno słowo w tytule.
    """
    meta = note.metadata
    weighted_fields = [
        (meta.title.lower(), _TITLE_WEIGHT),
        (note.body.lower(), 1),
        (" ".join(meta.decisions).lower(), 1),
        (" ".join(meta.open_questions).lower(), 1),
        (" ".join(meta.action_items).lower(), 1),
        (" ".join(meta.tags).lower(), 1),
        (" ".join(meta.participants).lower(), 1),
    ]
    matched: set[str] = set()
    field_bonus = 0
    for term in terms:
        for text, weight in weighted_fields:
            if term in text:
                matched.add(term)
                field_bonus += weight
    return len(matched) * _COVERAGE_WEIGHT + field_bonus


def _summarize(note: Note, terms: list[str], score: int) -> NoteSummary:
    """Zbuduj lekki wynik wyszukiwania z fragmentem wokół dopasowania."""
    return NoteSummary(
        id=note.id,
        title=note.metadata.title,
        project=note.metadata.project,
        date=note.metadata.date,
        participants=note.metadata.participants,
        snippet=_make_snippet(note.body, terms),
        score=score,
    )


def _make_snippet(body: str, terms: list[str]) -> str:
    """Wytnij fragment treści wokół pierwszego trafionego słowa (albo początek)."""
    text = " ".join(body.split())
    if not text:
        return ""
    low = text.lower()
    position = -1
    for term in terms:
        found = low.find(term)
        if found != -1:
            position = found
            break
    if position == -1:
        snippet = text[:_SNIPPET_LENGTH]
        return snippet + ("…" if len(text) > _SNIPPET_LENGTH else "")

    start = max(0, position - _SNIPPET_LENGTH // 3)
    end = min(len(text), start + _SNIPPET_LENGTH)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"
