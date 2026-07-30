"""Digest „co się zmieniło od <data>" — deterministyczna PROJEKCJA zdarzeń (ADR 0052, F5).

Odpowiednik ``ProjectBrief`` dla zdarzeń warstwy spajającej: fold zdarzeń od zadanej daty w
zwięzły przegląd (łącznie + wg źródła + per projekt: liczniki wg typu i czas ostatniego). Render
jest CZYSTY (same przechowane fakty, bez LLM), więc bez powierzchni halucynacji — treść zdarzeń to
DANE, nie polecenia. Skielet renderu współdzieli formatery z ``ProjectBrief`` (ADR 0051).

``truncated`` sygnalizuje, że okno mogło mieć więcej zdarzeń niż zeskanowano (sufit) — brak notatki
oznaczałby ciche ucięcie licznika. Kolejność sekcji jest deterministyczna (malejąco po liczbie).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from workmate.core.domain.text_format import fmt_counts, fmt_date, fmt_seconds

# Etykieta grupy zdarzeń bez przypisanego projektu (ADR 0028: część zdarzeń nie ma atrybucji).
UNASSIGNED = "(nieprzypisane)"


@dataclass(frozen=True)
class ProjectChanges:
    """Zmiany jednego projektu w oknie: liczba, rozbicie wg typu, czas ostatniego zdarzenia."""

    project: str
    total: int
    by_kind: tuple[tuple[str, int], ...]
    latest: datetime | None


@dataclass(frozen=True)
class ChangeDigest:
    """Przegląd zdarzeń od ``since``: łącznie, wg źródła i per projekt (posortowane)."""

    since: date
    total: int
    by_source: tuple[tuple[str, int], ...]
    projects: tuple[ProjectChanges, ...]
    truncated: bool

    def to_text(self) -> str:
        """Złóż digest jako Markdown: nagłówek, łącznie + wg źródła, sekcje per projekt."""
        lines = [
            f"# Co się zmieniło od {fmt_date(self.since)}",
            "",
            f"**Zdarzeń łącznie:** {self.total} · {fmt_counts(self.by_source)}",
        ]
        if not self.total:
            lines += ["", "_(brak zdarzeń w tym oknie)_"]
            return "\n".join(lines)
        for changes in self.projects:
            lines += [
                "",
                f"## {changes.project or UNASSIGNED} — {changes.total}",
                f"{fmt_counts(changes.by_kind)} (ostatnia: {fmt_seconds(changes.latest)})",
            ]
        if self.truncated:
            lines += ["", "_(okno ucięte do najnowszych zdarzeń — mogło ich być więcej.)_"]
        return "\n".join(lines)
