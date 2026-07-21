"""Treść prywatnej wiadomości z kartą czasu (ADR 0035) — czysty HTML dla Teams.

Dlaczego HTML składamy TU, w rdzeniu, a nie w adapterze: bo to jedyny sposób, żeby ominięcie
``to_teams_html`` w adapterze było bezpieczne. Zwykła ścieżka wysyłki escapuje surowy HTML
(``MarkdownIt("commonmark", {"html": False})``) i nie włącza tabel — czyli tabela dotarłaby jako
``&lt;table&gt;``. Metoda ``send_chat_html`` przepuszcza HTML bez zmian, więc kontrakt jest taki:
**każdy bajt tej wiadomości pochodzi z tej funkcji**, a każda wartość niesiona z zewnątrz (nazwa
osoby, klucz zgłoszenia, komentarz, ścieżka pliku) przechodzi przez ``html.escape``. Szkielet jest
sztywny i literalny — nie ma tu żadnej ścieżki, którą dane mogłyby wstrzyknąć znacznik.

To odróżnia ten przypadek od mostu zdarzeń, gdzie treść pochodzi z GitHuba/Jiry i dlatego MUSI iść
przez escapujący renderer. Tam treść jest niezaufana; tu jest nasza.
"""

from __future__ import annotations

from datetime import timedelta
from html import escape

from workmate.core.domain.timesheet import PersonTimesheet

# Stopka mówi wprost, że ponowny import tego samego arkusza zdubluje wpisy. To jedyna obrona
# przed tym ryzykiem — importu dokonuje CZŁOWIEK, więc nasza idempotencja go nie obejmuje
# (ADR 0035 § Consequences). Etykieta tygodnia w temacie i w nazwie pliku daje mu szansę
# zauważyć, że dostał ten sam arkusz drugi raz.
_FOOTER = (
    "Zaimportuj plik w Jirze: <b>Apps → WorklogPRO → Import worklogs</b>. "
    "Ponowny import tego samego arkusza <b>zdubluje</b> wpisy — jeśli już to zrobiłeś, pomiń."
)
_STYLE_TABLE = "border-collapse:collapse"
_STYLE_CELL = "border:1px solid #ccc;padding:4px 8px"
_STYLE_NUM = "border:1px solid #ccc;padding:4px 8px;text-align:right"


def render_timesheet_message(timesheet: PersonTimesheet, *, file_path: str = "") -> str:
    """Złóż wiadomość 1:1 z kartą czasu: nagłówek, tabela wpisów, suma, ścieżka pliku i stopka.

    Zakłada, że osoba PRACOWAŁA (``timesheet.worked()``) — pustej karty nie wysyłamy w ogóle,
    więc nie ma tu gałęzi „brak godzin". Wołający sprawdza predykat przed renderem.
    """
    name = escape(timesheet.person.display_name or timesheet.person.source_id)
    week = escape(timesheet.week_label or f"{timesheet.week_start}–{timesheet.week_end}")
    span = f"{timesheet.week_start.isoformat()} – {_last_day(timesheet)}"
    parts = [
        f"<p>Cześć {name}! Twoje godziny za tydzień <b>{week}</b> ({escape(span)}):</p>",
        _table(timesheet),
        f"<p>Razem: <b>{timesheet.total_hours} h</b></p>",
    ]
    if file_path:
        # Ścieżka jako TEKST, nie link: to lokalizacja w sieci firmowej, a klikalny odnośnik
        # w wiadomości od bota jest wzorcem, którego nie chcemy uczyć ludzi ufać.
        parts.append(f"<p>Arkusz do importu: <code>{escape(file_path)}</code></p>")
    parts.append(f"<p>{_FOOTER}</p>")
    return "".join(parts)


def _table(timesheet: PersonTimesheet) -> str:
    """Tabela wpisów: dzień, zgłoszenie, godziny — jeden wiersz na wpis, jak w arkuszu."""
    head = (
        f'<tr><th style="{_STYLE_CELL}">Dzień</th>'
        f'<th style="{_STYLE_CELL}">Zgłoszenie</th>'
        f'<th style="{_STYLE_CELL}">Godziny</th></tr>'
    )
    rows = [
        f'<tr><td style="{_STYLE_CELL}">{escape(entry.day.isoformat())}</td>'
        f'<td style="{_STYLE_CELL}">{escape(entry.issue_key)}</td>'
        f'<td style="{_STYLE_NUM}">{_hours(entry.minutes)}</td></tr>'
        for entry in timesheet.entries
    ]
    return f'<table style="{_STYLE_TABLE}">{head}{"".join(rows)}</table>'


def _hours(minutes: int) -> str:
    """Godziny dziesiętne jako tekst — liczba nie wymaga escapowania, ale trzymamy jeden format."""
    return f"{round(minutes / 60, 2)}"


def _last_day(timesheet: PersonTimesheet) -> str:
    """Ostatni dzień okna (``week_end`` jest półotwarty, więc pokazujemy dzień wcześniej)."""
    return (timesheet.week_end - timedelta(days=1)).isoformat()
