"""Niemutowalne modele domenowe: prompt, commit, podsumowanie dnia i raport.

Znaczniki czasu trzymamy jako świadome (aware) ``datetime`` w strefie źródła (UTC dla
promptów, offset commita dla ``git``). Konwersję na strefę lokalną robi WYŁĄCZNIE grupowanie
po dniu (``core.grouping``) — jedno miejsce decyduje, do którego dnia trafia zdarzenie.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Prompt:
    """Realny, wpisany przez człowieka prompt do Claude Code (nie tool-result, nie sub-agent).

    ``text`` jest już PO redakcji (patrz ``core.redaction``) — wrażliwe wartości zastąpione
    etykietami. ``redactions`` to zbiór kategorii, które usunięto (audyt; nie zawiera wartości).
    """

    timestamp: datetime
    text: str
    session_id: str
    cwd: str
    project: str  # nazwa folderu w ~/.claude/projects (nazwa użytkownika zredagowana)
    redactions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Commit:
    """Pojedynczy commit z ``git log`` (data autora, nie commitera)."""

    sha: str
    timestamp: datetime
    author: str
    message: str


@dataclass(frozen=True)
class DaySummary:
    """Zestawienie jednego dnia: prompty + commity, opcjonalnie opis prozą z LLM."""

    day: date
    prompts: tuple[Prompt, ...]
    commits: tuple[Commit, ...]
    llm_prose: str | None = None

    @property
    def prompt_count(self) -> int:
        return len(self.prompts)

    @property
    def commit_count(self) -> int:
        return len(self.commits)

    @property
    def is_empty(self) -> bool:
        return not self.prompts and not self.commits


@dataclass(frozen=True)
class SummaryReport:
    """Pełny raport za zakres dat dla jednej osoby (dni pokrywają CAŁY zakres, także puste)."""

    person: str
    since: date
    until: date
    repo: str | None
    days: tuple[DaySummary, ...]
