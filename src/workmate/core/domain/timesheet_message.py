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
from workmate.core.domain.timesheet_sheet import cell_text, format_time_spent

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


def render_timesheet_message(
    timesheet: PersonTimesheet, *, file_path: str = "", attached: bool = False
) -> str:
    """Złóż wiadomość 1:1 z kartą czasu: nagłówek, tabela wpisów, suma, wskazanie pliku i stopka.

    Zakłada, że osoba PRACOWAŁA (``timesheet.worked()``) — pustej karty nie wysyłamy w ogóle,
    więc nie ma tu gałęzi „brak godzin". Wołający sprawdza predykat przed renderem.

    Dwie ROZŁĄCZNE ścieżki wskazania arkusza (ADR 0035/0038):
    - ``attached=True`` — plik jedzie ZAŁĄCZNIKIEM tej wiadomości (dostawa przez ``UserDocSender``);
      mówimy „w załączniku" i NIE pokazujemy ścieżki serwerowej (odbiorca i tak jej nie dosięga).
    - w przeciwnym razie, gdy ``file_path`` niepuste — fallback: pokazujemy ścieżkę pliku TEKSTEM.
    ``attached`` ma pierwszeństwo: przy dostawie załącznikiem ścieżka jest bez znaczenia.
    """
    name = escape(timesheet.person.display_name or timesheet.person.source_id)
    week = escape(timesheet.week_label or f"{timesheet.week_start}–{timesheet.week_end}")
    span = f"{timesheet.week_start.isoformat()} – {_last_day(timesheet)}"
    total = format_time_spent(timesheet.total_minutes)
    parts = [
        f"<p>Cześć {name}! Twoje godziny za tydzień <b>{week}</b> ({escape(span)}):</p>",
        _table(timesheet),
        # Suma w tej samej notacji co wiersze ORAZ dziesiętnie — dziesiętna jest do porównania
        # z ewidencją, notacja Jiry do porównania z arkuszem. Obie liczone z MINUT, więc żadna
        # nie jest sumą zaokrągleń.
        f"<p>Razem: <b>{total}</b> ({timesheet.total_hours} h)</p>",
    ]
    if attached:
        parts.append("<p>📎 Arkusz importu WorklogPRO jest w <b>załączniku</b> tej wiadomości.</p>")
    elif file_path:
        # Ścieżka jako TEKST, nie link: to lokalizacja w sieci firmowej, a klikalny odnośnik
        # w wiadomości od bota jest wzorcem, którego nie chcemy uczyć ludzi ufać.
        parts.append(f"<p>Arkusz do importu: <code>{escape(file_path)}</code></p>")
    parts.append(f"<p>{_FOOTER}</p>")
    return "".join(parts)


def _table(timesheet: PersonTimesheet) -> str:
    """Tabela wpisów: dzień, zgłoszenie, czas — DOKŁADNIE te wiersze, które trafiły do arkusza.

    Dwie decyzje trzymają wiadomość i arkusz w zgodzie, bo człowiek porównuje je obok siebie
    i każda różnica podważa jego zaufanie do obu:

    **Ten sam filtr.** Wpisy zerowe (urlop) odpada projekcja arkusza, więc odpadają i tutaj —
    inaczej wiadomość obiecywała więcej wierszy, niż plik zawierał.

    **Ta sama notacja.** Czas per wiersz szedł jako godziny dziesiętne zaokrąglone do dwóch
    miejsc, a „Razem" liczyło się z MINUT — suma kolumny nie zgadzała się więc z sumą pod nią
    (3 × 50 min = 0.83 + 0.83 + 0.83 ≠ 2.5). Notacja Jiry jest dokładna i identyczna z arkuszem.
    """
    head = (
        f'<tr><th style="{_STYLE_CELL}">Dzień</th>'
        f'<th style="{_STYLE_CELL}">Zgłoszenie</th>'
        f'<th style="{_STYLE_CELL}">Czas</th></tr>'
    )
    rows = [
        f'<tr><td style="{_STYLE_CELL}">{escape(entry.day.isoformat())}</td>'
        f'<td style="{_STYLE_CELL}">{escape(cell_text(entry.issue_key))}</td>'
        f'<td style="{_STYLE_NUM}">{format_time_spent(entry.minutes)}</td></tr>'
        for entry in timesheet.entries
        if entry.minutes > 0
    ]
    return f'<table style="{_STYLE_TABLE}">{head}{"".join(rows)}</table>'


def _last_day(timesheet: PersonTimesheet) -> str:
    """Ostatni dzień okna (``week_end`` jest półotwarty, więc pokazujemy dzień wcześniej)."""
    return (timesheet.week_end - timedelta(days=1)).isoformat()
