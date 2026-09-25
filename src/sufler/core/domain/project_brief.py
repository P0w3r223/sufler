"""One-pager projektu — deterministyczna PROJEKCJA stanu (ADR 0051, F4).

``ProjectBrief`` opakowuje gotowy ``ProjectStatus`` (pełna synteza: rejestr + fakty z notatek +
aktywność GitHub, ADR 0029) i dokłada listę OSTATNICH notatek. Zero duplikacji pól statusu —
``ProjectStatus`` zostaje jedynym źródłem prawdy, brief tylko komponuje go z notatkami i renderuje.

``to_text`` składa zwięzły one-pager (Markdown, czytelny inline w Teams i jako źródło PDF). Render
jest CZYSTY — same przechowane fakty, bez LLM, więc bez powierzchni halucynacji (treść notatek i
statusu to DANE, nie polecenia). Ten sam skielet renderu dziedziczą później F5/F6.
"""

from __future__ import annotations

from dataclasses import dataclass

from sufler.core.domain.models import NoteSummary, ProjectStatus
from sufler.core.domain.text_format import fmt_date, fmt_seconds

# Sufit notatek listowanych w one-pagerze — brief STRESZCZA stan, nie jest pełnym indeksem.
DEFAULT_BRIEF_NOTES = 5


@dataclass(frozen=True)
class ProjectBrief:
    """Status projektu + ostatnie notatki, gotowe do jednego czytelnego one-pagera."""

    status: ProjectStatus
    recent_notes: tuple[NoteSummary, ...]

    def to_text(self) -> str:
        """Złóż one-pager jako Markdown: nagłówek, status, liczby, ostatnie notatki."""
        s = self.status
        lines = [
            f"# One-pager: {s.name} ({s.company}/{s.key})",
            "",
            f"**Status:** {s.status} · **zdrowie:** {s.health} · **faza:** {s.phase}",
            f"**Zaktualizowano:** {fmt_date(s.last_updated)}",
        ]
        if s.summary.strip():
            lines += ["", s.summary.strip()]
        lines += [
            "",
            f"**Notatki:** {s.notes_count} (ostatnia: {fmt_date(s.latest_note_date)}) · "
            f"**otwarte action items:** {s.open_action_items}",
            f"**Aktywność GitHub:** {s.recent_activity_count} zdarzeń "
            f"(ostatnia: {fmt_seconds(s.latest_activity_at)}) · "
            f"**nieudane CI:** {s.failing_ci_count}",
            "",
            "## Ostatnie notatki",
        ]
        if self.recent_notes:
            lines += [_note_line(n) for n in self.recent_notes]
        else:
            lines.append("_(brak notatek dla tego projektu)_")
        return "\n".join(lines)


def _note_line(note: NoteSummary) -> str:
    """Jeden wiersz listy notatek: ``- <data> — <tytuł> (<uczestnicy>)``."""
    who = ", ".join(note.participants) if note.participants else "brak uczestników"
    return f"- {fmt_date(note.date)} — {note.title} ({who})"
