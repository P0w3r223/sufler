"""Domena karty czasu pracy (ADR 0035) — wpisy ze źródła → zestawienie per osoba.

CZYSTA agregacja: bez I/O, bez SDK, bez zegara. Wejściem są wpisy już odczytane przez adapter
źródła (``HoursSource``), wyjściem zestawienie gotowe do dwóch rzeczy naraz: zrzutu do arkusza
WorklogPRO i do tabeli w wiadomości Teams. Jedno źródło liczb dla obu — inaczej człowiek
zobaczyłby w czacie inną sumę niż w pliku, który ma zaimportować.

Rachunki prowadzimy w MINUTACH (``int``), godziny wyprowadzamy — sumy dzienne i per zgłoszenie
zgadzają się wtedy DOKŁADNIE z całością, bez dryfu float. Ta sama dyscyplina co
``core/domain/worklog.py``.

Kluczowa różnica wobec ADR 0034: tu nie ma ESTYMACJI. Godziny pochodzą ze źródła, które je zna;
domena ich nie zgaduje, tylko grupuje, sumuje i pilnuje, żeby nie wyciekły do niewłaściwej osoby.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from workmate.core.errors import WorkMateError


class TimesheetError(WorkMateError):
    """Dane karty czasu są niespójne albo nieprawdopodobne — osoba trafia do raportu jako błąd."""


class Person(BaseModel):
    """Rozwiązana tożsamość pracownika — identyfikatory z różnych systemów, żaden z pozostałych.

    ``source_id`` to klucz ze źródła godzin, ``aad_user_id`` adresuje czat Teams i wskazuje osobę
    w grafiku Shifts, ``jira_user`` (e-mail albo accountId) trafia do kolumny ``User`` arkusza.
    ``git_email`` (OPCJONALNY) to most do commitów i ``claude_summary`` — pozwala przypisać pracę
    danego dnia do zgłoszeń Jira (klucze z commitów) i opisu (ADR 0036). Puste ``git_email`` znaczy
    „brak atrybucji per-commit": godziny tej osoby trafią w całości na koszykowe issue.

    Mapowanie jest jawną konfiguracją, nie dopasowaniem po nazwisku: zły ``jira_user`` wpisze czyjeś
    godziny na CUDZE konto Jiry, a worklogi są create-only i nieusuwalne narzędziem (ADR 0034).
    """

    source_id: str
    aad_user_id: str
    jira_user: str
    display_name: str = ""
    git_email: str = ""


class WorkEntry(BaseModel):
    """Pojedynczy wpis czasu ze źródła: kto, kiedy, na czym, ile.

    ``minutes`` zamiast godzin, bo to jednostka bez strat. ``comment`` jest opcjonalny i trafia
    do arkusza — to DANE ze źródła zewnętrznego, więc przechodzi przez sanityzację w projekcji
    (``timesheet_sheet``: znaki sterujące) i escapowanie w renderze wiadomości (``html.escape``).
    Sanityzacja mieszka w RDZENIU, nie w adapterze źródła: obowiązuje wtedy każde źródło godzin,
    także to, którego jeszcze nie napisaliśmy.
    """

    source_id: str
    day: date
    issue_key: str
    minutes: int
    comment: str = ""


class DayTotal(BaseModel):
    """Suma czasu w jednej dobie (do tabeli w wiadomości)."""

    day: date
    minutes: int
    hours: float
    issue_keys: tuple[str, ...] = ()


class IssueTotal(BaseModel):
    """Suma czasu na jednym zgłoszeniu w całym tygodniu."""

    issue_key: str
    minutes: int
    hours: float


class PersonTimesheet(BaseModel):
    """Komplet danych jednej osoby za jeden tydzień — podstawa arkusza ORAZ wiadomości."""

    person: Person
    week_start: date
    week_end: date
    week_label: str = ""
    entries: tuple[WorkEntry, ...] = ()
    by_day: tuple[DayTotal, ...] = ()
    by_issue: tuple[IssueTotal, ...] = ()
    total_minutes: int = 0
    total_hours: float = 0.0

    def worked(self) -> bool:
        """Czy ta osoba w ogóle pracowała w tym tygodniu — bramka wysyłki wiadomości.

        Nazwany predykat, nie inline ``if``: to on decyduje, czy człowiek dostanie w piątek
        prywatną wiadomość, czy cisza. Warunek jest podwójny celowo — wpisy o zerowym czasie
        (źródło potrafi je zwrócić dla dni urlopowych) nie są pracą.
        """
        return bool(self.entries) and self.total_minutes > 0


def build_timesheet(
    person: Person,
    entries: list[WorkEntry],
    *,
    week_start: date,
    week_end: date,
    week_label: str = "",
    max_minutes_per_day: int = 0,
) -> PersonTimesheet:
    """Złóż zestawienie jednej osoby: filtr okna, sumy dzienne i per zgłoszenie, kontrola zdrowia.

    Okno ``[week_start, week_end)`` jest półotwarte — spójnie z ``domain/week.reported_week``,
    więc dzień styku nie wpadnie do dwóch tygodni naraz.

    ``max_minutes_per_day`` (0 = bez limitu) to strażnik jakości danych, nie polityka kadrowa:
    źródło potrafi się zaciąć i zwrócić absurd, a my mamy wysłać to człowiekowi i wpisać do Jiry.
    Lepiej zgłosić błąd tej jednej osoby, niż rozesłać nieprawdopodobne liczby.
    """
    mine = [e for e in entries if e.source_id == person.source_id]
    in_window = [e for e in mine if week_start <= e.day < week_end]
    _reject_negative(in_window)
    by_day = _totals_by_day(in_window)
    if max_minutes_per_day:
        _reject_impossible_days(person, by_day, max_minutes_per_day)
    total = sum(e.minutes for e in in_window)
    return PersonTimesheet(
        person=person,
        week_start=week_start,
        week_end=week_end,
        week_label=week_label,
        entries=tuple(sorted(in_window, key=lambda e: (e.day, e.issue_key))),
        by_day=by_day,
        by_issue=_totals_by_issue(in_window),
        total_minutes=total,
        total_hours=hours_of(total),
    )


def group_by_source_id(entries: list[WorkEntry]) -> dict[str, list[WorkEntry]]:
    """Pogrupuj wpisy po ``source_id`` — wejście do pętli „jedna osoba, jeden arkusz"."""
    grouped: dict[str, list[WorkEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.source_id, []).append(entry)
    return grouped


def hours_of(minutes: int) -> float:
    """Minuty → godziny dziesiętne (2 miejsca). Prezentacja; źródłem prawdy są minuty."""
    return round(minutes / 60, 2)


def _reject_negative(entries: list[WorkEntry]) -> None:
    """Ujemny czas to zawsze błąd źródła — nie ma sensownej interpretacji, więc odrzucamy."""
    for entry in entries:
        if entry.minutes < 0:
            raise TimesheetError(
                f"wpis odrzucony: {entry.issue_key} z {entry.day} ma ujemny czas "
                f"({entry.minutes} min) — źródło godzin zwróciło niespójne dane."
            )


def _reject_impossible_days(
    person: Person, by_day: tuple[DayTotal, ...], max_minutes_per_day: int
) -> None:
    """Odrzuć dobę przekraczającą sufit zdrowego rozsądku (domyślnie z konfiguracji)."""
    for day in by_day:
        if day.minutes > max_minutes_per_day:
            raise TimesheetError(
                f"zestawienie odrzucone: {person.source_id} ma {hours_of(day.minutes)} h "
                f"w dniu {day.day}, powyżej limitu {hours_of(max_minutes_per_day)} h "
                "— sprawdź źródło godzin zamiast wysyłać te liczby."
            )


def _totals_by_day(entries: list[WorkEntry]) -> tuple[DayTotal, ...]:
    """Zsumuj wpisy w doby (rosnąco po dacie)."""
    buckets: dict[date, list[WorkEntry]] = {}
    for entry in entries:
        buckets.setdefault(entry.day, []).append(entry)
    totals = []
    for day in sorted(buckets):
        same_day = buckets[day]
        minutes = sum(e.minutes for e in same_day)
        keys: dict[str, None] = {}
        for entry in sorted(same_day, key=lambda e: e.issue_key):
            keys.setdefault(entry.issue_key, None)
        totals.append(
            DayTotal(day=day, minutes=minutes, hours=hours_of(minutes), issue_keys=tuple(keys))
        )
    return tuple(totals)


def _totals_by_issue(entries: list[WorkEntry]) -> tuple[IssueTotal, ...]:
    """Zsumuj wpisy per zgłoszenie, malejąco po czasie (najpierw to, na czym praca stała)."""
    minutes_by_key: dict[str, int] = {}
    for entry in entries:
        minutes_by_key[entry.issue_key] = minutes_by_key.get(entry.issue_key, 0) + entry.minutes
    return tuple(
        IssueTotal(issue_key=key, minutes=minutes_by_key[key], hours=hours_of(minutes_by_key[key]))
        for key in sorted(minutes_by_key, key=lambda k: (-minutes_by_key[k], k))
    )
