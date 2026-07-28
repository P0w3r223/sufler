"""Karta czasu NA ŻĄDANIE dla JEDNEJ osoby (drzwi worklog self-service).

Jednoosobowy, interaktywny odpowiednik wsadowego ``WeeklyTimesheetService`` (ADR 0035): zamiast
piątkowej pętli po całym zespole, jeden obrót dla nadawcy, który SAM przysłał swój
``claude_summary``. Składa REALNE minuty z Microsoft Shifts (nadawcy) + klucze issue i opisy
z PRZYSŁANEGO JSON-a (``SubmittedSummary``) → ``PersonTimesheet`` → arkusz WorklogPRO. Tożsamość
jest już rozwiązana z uwierzytelnionego nadawcy (drzwi), więc tu jej nie zgadujemy — dostajemy
gotowy ``Person``.

Okno bierzemy z SUBMISJI (``since``/``until``), nie z ``reported_week``: to użytkownik decyduje, za
który tydzień prosi o arkusz. ``claude_summary`` raportuje ``until`` WŁĄCZNIE, więc domykamy okno do
PÓŁOTWARTEGO (``until + 1 dzień``), inaczej ostatni dzień (niedziela) wypadłby z Shifts i z karty.

Dwa strażniki izolacji osoby: bierzemy tylko bloki zmian o ``aad_user_id`` nadawcy (Graph potrafi
oddać cały zespół), a ``build_timesheet`` dodatkowo filtruje po ``source_id``. Godziny to REALNE
minuty zmian (bez estymacji, ADR 0036); dzień bez kluczy z commitów → koszykowe issue.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from workmate.core.domain.issue_attribution import split_day_minutes
from workmate.core.domain.shift_hours import ShiftBlock, minutes_by_person_day
from workmate.core.domain.submitted_summary import (
    SubmissionRejected,
    SubmittedSummary,
    parse_submitted_summary,
    verify_submission_owner,
)
from workmate.core.domain.timesheet import Person, PersonTimesheet, WorkEntry, build_timesheet
from workmate.core.domain.timesheet_message import render_timesheet_message
from workmate.core.domain.timesheet_sheet import project_sheet, sheet_filename
from workmate.core.domain.week import week_label

if TYPE_CHECKING:
    from workmate.core.ports.timesheets import SheetWriter, ShiftSource

# Powody wyniku — stałe, nie wolny tekst: raport operatora i logi zliczają się między przebiegami.
REASON_OK = "ok"
REASON_REJECTED = "rejected"
REASON_NO_WORK = "no_work"


@dataclass(frozen=True)
class SelfServiceResult:
    """Wynik jednego żądania: gotowe zestawienie + ścieżka zapisanego arkusza.

    ``file_path`` puste = nic nie zapisano, bo nadawca nie ma godzin w oknie (``worked()`` fałsz);
    drzwi odpowiadają wtedy komunikatem „brak godzin", a nie pustym plikiem.
    """

    timesheet: PersonTimesheet
    file_path: str = ""


def build_selfservice_timesheet(
    person: Person,
    blocks: list[ShiftBlock],
    summary: SubmittedSummary,
    *,
    tz: ZoneInfo,
    fallback_issue: str,
    max_minutes_per_day: int = 0,
) -> PersonTimesheet:
    """Złóż zestawienie nadawcy: minuty Shifts × klucze/opisy z submisji, w oknie z submisji."""
    week_start = summary.since
    week_end = summary.until + timedelta(days=1)  # until WŁĄCZNIE (claude_summary) → półotwarte
    entries = _entries(
        person,
        blocks,
        summary,
        since=week_start,
        until=week_end,
        tz=tz,
        fallback_issue=fallback_issue,
    )
    label = week_label(datetime.combine(week_start, time.min, tzinfo=tz))
    return build_timesheet(
        person,
        entries,
        week_start=week_start,
        week_end=week_end,
        week_label=label,
        max_minutes_per_day=max_minutes_per_day,
    )


def _entries(
    person: Person,
    blocks: list[ShiftBlock],
    summary: SubmittedSummary,
    *,
    since: date,
    until: date,
    tz: ZoneInfo,
    fallback_issue: str,
) -> list[WorkEntry]:
    """Minuty per dzień (tylko nadawcy) rozdzielone na klucze z submisji; reszta na koszyk."""
    entries: list[WorkEntry] = []
    for row in minutes_by_person_day(blocks, since=since, until=until, tz=tz):
        if row.user_id != person.aad_user_id:
            continue  # Graph oddaje cały zespół — bierzemy WYŁĄCZNIE zmiany nadawcy (izolacja)
        keys = list(summary.issue_keys_by_day.get(row.day, ()))
        comment = summary.comments_by_day.get(row.day, "")
        for issue_key, share in split_day_minutes(row.minutes, keys, fallback_issue=fallback_issue):
            entries.append(
                WorkEntry(
                    source_id=person.source_id,
                    day=row.day,
                    issue_key=issue_key,
                    minutes=share,
                    comment=comment,
                )
            )
    return entries


class SelfServiceWorklog:
    """Serwis na żądanie: Shifts nadawcy + submisja → arkusz WorklogPRO w ``output_dir``."""

    def __init__(
        self,
        shifts: ShiftSource,
        sheets: SheetWriter,
        *,
        output_dir: str,
        tz: ZoneInfo,
        fallback_issue: str,
        start_hour: int = 8,
        max_minutes_per_day: int = 0,
    ) -> None:
        self._shifts = shifts
        self._sheets = sheets
        self._output_dir = output_dir.rstrip("/\\")
        self._tz = tz
        self._fallback_issue = fallback_issue
        self._start_hour = start_hour
        self._max_minutes_per_day = max_minutes_per_day

    def run(self, person: Person, summary: SubmittedSummary) -> SelfServiceResult:
        """Zbuduj i zapisz arkusz nadawcy; bez godzin w oknie zwróć wynik bez pliku."""
        blocks = self._shifts.read_blocks()
        timesheet = build_selfservice_timesheet(
            person,
            blocks,
            summary,
            tz=self._tz,
            fallback_issue=self._fallback_issue,
            max_minutes_per_day=self._max_minutes_per_day,
        )
        if not timesheet.worked():
            return SelfServiceResult(timesheet=timesheet)
        path = f"{self._output_dir}/{sheet_filename(timesheet)}"
        sheet = project_sheet(timesheet, start_hour=self._start_hour, tz=self._tz)
        self._sheets.write(path, sheet.headers, sheet.rows)
        return SelfServiceResult(timesheet=timesheet, file_path=path)


@dataclass(frozen=True)
class SelfServiceOutcome:
    """Wynik obsługi JEDNEJ submisji: odpowiedź HTML do DM + metadane dla raportu/idempotencji.

    ``is_success`` = powstał arkusz z godzinami (jedyny przypadek, który drzwi zapamiętują jako
    obsłużony i liczą jako „wysłany"). Odrzucenie i brak godzin też dają odpowiedź do nadawcy, ale
    NIE są sukcesem — nadawca może poprawić i wysłać ponownie.
    """

    reply_html: str
    reason: str
    file_path: str = ""
    week_label: str = ""
    total_minutes: int = 0
    is_success: bool = False


def handle_submission(
    worklog: SelfServiceWorklog,
    person: Person,
    payload: Any,
    *,
    deliver_as_attachment: bool = False,
) -> SelfServiceOutcome:
    """Obsłuż jedną submisję ROZWIĄZANEJ już osoby: parsuj → zweryfikuj → złóż → odpowiedź.

    CZYSTA orkiestracja (bez I/O poza portem ``SheetWriter`` w serwisie): rozwiązanie tożsamości
    (aad nadawcy w trybie live, ``source_id`` w trybie operatora) należy do DRZWI — tu dostajemy
    gotowy ``Person``, więc handler jest jeden dla obu trybów i w całości testowalny na atrapach.
    Idempotencję (dedup submisji) i samą WYSYŁKĘ też robią drzwi; handler tylko liczy odpowiedź.

    ``deliver_as_attachment`` — gdy drzwi odeślą arkusz ZAŁĄCZNIKIEM (ADR 0038 przez 0027), treść
    mówi „w załączniku" i nie pokazuje ścieżki serwerowej. ``file_path`` w wyniku zostaje ZAWSZE:
    drzwi potrzebują go, by wczytać bajty do wgrania. Domyślnie fallback ścieżką (jak dotąd).

    Tryb treści bierzemy z tego samego predykatu, którym drzwi wybierają MECHANIZM (jest plik do
    wgrania): „w załączniku" tylko gdy realnie jest co załączyć. Bez tego pęknięty inwariant (sukces
    bez ścieżki) dałby treść „arkusz w załączniku" przy dostawie tekstem — obietnica bez pliku.
    """
    try:
        summary = parse_submitted_summary(payload)
        verify_submission_owner(person, summary.person_git_email)
    except SubmissionRejected as exc:
        return SelfServiceOutcome(reply_html=_reject_html(exc), reason=REASON_REJECTED)

    result = worklog.run(person, summary)
    timesheet = result.timesheet
    if not timesheet.worked():
        return SelfServiceOutcome(
            reply_html=_no_hours_html(summary),
            reason=REASON_NO_WORK,
            week_label=timesheet.week_label,
        )
    reply_html = (
        render_timesheet_message(timesheet, attached=True)
        if deliver_as_attachment and result.file_path
        else render_timesheet_message(timesheet, file_path=result.file_path)
    )
    return SelfServiceOutcome(
        reply_html=reply_html,
        reason=REASON_OK,
        file_path=result.file_path,
        week_label=timesheet.week_label,
        total_minutes=timesheet.total_minutes,
        is_success=True,
    )


def _reject_html(exc: SubmissionRejected) -> str:
    """Odpowiedź o odrzuceniu — treść wyjątku ESCAPUJEMY (niesie e-mail z submisji)."""
    return f"⚠️ Nie wygenerowałem arkusza: {html.escape(str(exc))}"


def _no_hours_html(summary: SubmittedSummary) -> str:
    """Odpowiedź, gdy nadawca nie ma opublikowanych godzin w oknie submisji (nie sukces)."""
    return (
        f"ℹ️ Nie znalazłem Twoich godzin w Microsoft Shifts za okno "
        f"{html.escape(summary.since.isoformat())}–{html.escape(summary.until.isoformat())}. "
        "Sprawdź, czy zmiany są OPUBLIKOWANE (nie robocze), i wyślij ponownie."
    )
