"""Projekcja karty czasu na arkusz importu WorklogPRO (ADR 0035) — czysta, bez openpyxl.

Rdzeń produkuje ``Sheet`` (nagłówki + wiersze), adapter tylko zrzuca to do pliku. Podział jest
celowy: WSZYSTKO, co realnie może być błędne — nazwy kolumn, format czasu, offset ISO, filtr
osoby — jest tutaj, czyste i testowalne bez SDK. Adapter, który tego nie interpretuje, nie ma
jak się pomylić.

.. warning::
   **Nagłówki są NIEPOTWIERDZONE.** Pochodzą z dokumentacji producenta, której nie dało się
   zweryfikować co do wielkości liter i spacji; WorklogPRO dopasowuje kolumny PO NAZWIE, więc
   literówka unieważnia cały plik. Autorytatywnym źródłem jest szablon pobrany z kreatora
   (Apps → WorklogPRO → Import worklogs) na KONKRETNEJ instancji — bo jest generowany pod jej
   atrybuty niestandardowe. Do czasu potwierdzenia traktuj ``WORKLOGPRO_HEADERS`` jak hipotezę;
   korekta to zmiana tej jednej krotki i testu ``test_headers_are_not_changed_by_accident``
   (to DETEKTOR ZMIANY, nie bramka poprawności — nie wie, jak wyglądają prawdziwe nagłówki).
   Tryb BOJOWY drzwi nie wystartuje bez ``WORKMATE_WORKLOGI_HEADERS_CONFIRMED=true``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from workmate.core.domain.guards import assert_single_person
from workmate.core.domain.timesheet import PersonTimesheet

# HIPOTEZA — patrz ostrzeżenie w docstringu modułu. Kolejność kolumn jest dowolna (dopasowanie
# po nazwie), ale trzymamy ją stałą, żeby plik był powtarzalny i czytelny dla człowieka.
WORKLOGPRO_HEADERS: tuple[str, ...] = (
    "Issue Key/ID",
    "User",
    "Time Spent",
    "Start Date & Time",
    "Comment",
)

_MAX_COMMENT = 500
_FILENAME_RE = re.compile(r"[^a-z0-9]+")
# Litery, których NFKD NIE rozłoży, bo nie są bazą + diakrytem, tylko osobnymi znakami.
# Bez tego „Mikołaj" daje „miosz", a „Łukasz" — „ukasz". Reszta polskich znaków (ą, ę, ć, ń,
# ó, ś, ź, ż) rozkłada się poprawnie, więc wymieniamy tylko wyjątki.
_UNDECOMPOSABLE = str.maketrans({"ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ø": "o", "Ø": "O"})


@dataclass(frozen=True)
class Sheet:
    """Arkusz gotowy do zrzutu: nagłówki i wiersze. Adapter niczego już nie interpretuje."""

    headers: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


def project_sheet(
    timesheet: PersonTimesheet, *, start_hour: int, tz: ZoneInfo, comment_prefix: str = ""
) -> Sheet:
    """Zrzuć kartę czasu na wiersze importu — JEDEN wiersz na wpis (osoba, dzień, zgłoszenie).

    Granularność wymusza sam format: ``Start Date & Time`` jest polem WIERSZA, więc scalanie
    wpisów z różnych dni w jeden wiersz zgubiłoby datę.

    Strażnik osoby wołamy TU po raz drugi (pierwszy raz robi to ``build_timesheet``, filtrując
    wpisy). To nie jest paranoja: ta funkcja produkuje artefakt, który trafia do CUDZEJ Jiry,
    a zły wiersz jest po imporcie nieusuwalny narzędziem (ADR 0034). Sprawdzamy dwie rzeczy —
    pochodzenie wpisów oraz to, że każda komórka ``User`` niesie tę samą, właściwą tożsamość.
    """
    person = timesheet.person
    assert_single_person(person.source_id, (entry.source_id for entry in timesheet.entries))
    rows = tuple(
        (
            cell_text(entry.issue_key),
            person.jira_user,
            format_time_spent(entry.minutes),
            format_started(entry.day, start_hour=start_hour, tz=tz),
            _comment(entry.comment, comment_prefix),
        )
        for entry in timesheet.entries
        if entry.minutes > 0  # zerowe wpisy (urlop) nie są pracą — WorklogPRO i tak by je odrzucił
    )
    _assert_single_user_cell(person.jira_user, rows)
    return Sheet(headers=WORKLOGPRO_HEADERS, rows=rows)


def format_time_spent(minutes: int) -> str:
    """Minuty → czas w notacji Jiry (``2h 30m``, ``45m``, ``3h``).

    Świadomie BEZ jednostki dni (``1d``): w Jirze „dzień" to konfigurowalna liczba godzin
    (domyślnie 8), więc ``1d`` znaczy co innego na różnych instancjach. Godziny i minuty są
    jednoznaczne wszędzie — plik ma być przenośny, a nie zależny od ustawień projektu.
    """
    if minutes <= 0:
        raise ValueError(f"czas musi być dodatni, jest: {minutes} min")
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours}h {rest}m"
    return f"{hours}h" if hours else f"{rest}m"


def format_started(day: date, *, start_hour: int, tz: ZoneInfo) -> str:
    """Dzień → pełny znacznik ISO 8601 z JAWNYM offsetem (``2026-07-15T08:00:00.000+0200``).

    Pełny znacznik, nie sama data, i to z dwóch niezależnych powodów. Po pierwsze WorklogPRO
    przy dacie bez godziny sam dopisuje 08:00. Po drugie — groźniejsze — używa wtedy strefy
    PRZEGLĄDARKI importującego, więc ten sam plik dałby różne wyniki u różnych osób. Offset
    liczymy przez ``ZoneInfo``, więc jest poprawny po obu stronach zmiany czasu.
    """
    moment = datetime.combine(day, time(hour=start_hour), tzinfo=tz)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000%z")


def sheet_filename(timesheet: PersonTimesheet) -> str:
    """Deterministyczna nazwa pliku: ``worklog_<osoba>_<source_id>_<tydzien>.xlsx``.

    Etykieta tygodnia w nazwie jest częścią obrony przed podwójnym importem — człowiek widzi,
    że dostał TEN SAM arkusz drugi raz (nasza idempotencja jego importu nie obejmuje).
    Determinizm jest też potrzebny przy nadrabianiu: powtórzony przebieg ma nadpisać plik,
    a nie położyć drugi obok.

    ``source_id`` jest w nazwie OBOWIĄZKOWO, obok nazwy człowieka. Sama nazwa nie jest
    różnowartościowa: dwie osoby o tym samym imieniu i nazwisku dostawały tę samą ścieżkę, więc
    drugi arkusz NADPISYWAŁ pierwszy, a wiadomość pierwszej osoby wskazywała już cudze godziny.
    ``_slug`` dodatkowo zwęża przestrzeń — nazwa bez znaków ASCII daje stałe ``bez-nazwy``,
    czyli kolizję nawet przy różnych nazwiskach. Identyfikator ze źródła jest z definicji
    unikalny (to po nim rozdzielamy godziny), więc rozstrzyga ostatecznie.
    """
    person = timesheet.person
    who = _slug(person.display_name or person.source_id)
    week = _slug(timesheet.week_label or timesheet.week_start.isoformat())
    return f"worklog_{who}_{_slug(person.source_id)}_{week}.xlsx"


def _comment(comment: str, prefix: str) -> str:
    """Złóż komentarz wiersza (opcjonalny prefiks + treść ze źródła), przycięty do limitu."""
    return cell_text(f"{prefix}{comment}")[:_MAX_COMMENT]


def cell_text(raw: str) -> str:
    """Sprowadź tekst ze ŹRÓDŁA do jednej bezpiecznej linii komórki (bez znaków sterujących).

    ``WorkEntry`` deklarował, że treść ze źródła jest sanityzowana — i nikt tego nie robił
    (``JsonHoursSource`` woła samo ``str(...)``). Kontrakt zadeklarowany i niezaimplementowany
    jest gorszy niż jego brak: nikt go nie szuka. Egzekwujemy go TU, w projekcji, żeby
    obowiązywał niezależnie od tego, który adapter dostarczył godziny i który zapisze plik.

    Znaki sterujące WYCINAMY, nie rzucamy: jeden dziwny wpis nie może pozbawić całego zespołu
    arkuszy. Nowa linia i tab też lecą — w komórce rozbijają wiersz, a w ewentualnym eksporcie
    CSV rozbijają cały plik. Wstrzyknięcie FORMUŁY (``=cmd|…``) neutralizuje osobna warstwa:
    ``SheetWriter`` ma kontraktowy obowiązek zapisać każdą komórkę jako tekst — tego rdzeń
    zagwarantować nie może, bo nie wie, czym plik zostanie zapisany.
    """
    # Znak sterujący zamieniamy na SPACJĘ, nie usuwamy: „dwie\nlinie" ma dać „dwie linie",
    # a nie sklejone „dwielinie". Nadmiar białych znaków zwija się przy ``split``.
    kept = [
        " " if (ord(ch) < 0x20 or ord(ch) == 0x7F or 0x80 <= ord(ch) <= 0x9F) else ch
        for ch in str(raw or "")
    ]
    return " ".join("".join(kept).split())


def _assert_single_user_cell(expected: str, rows: tuple[tuple[Any, ...], ...]) -> None:
    """Ostatni strażnik przed zapisem: KAŻDA komórka ``User`` musi nieść tę samą tożsamość.

    Sprawdzamy wyprojektowane komórki, nie wpisy źródłowe — to one trafią do pliku. Gdyby
    projekcja kiedykolwiek zaczęła brać ``jira_user`` per wiersz, ten test wyłapie rozjazd.
    """
    user_column = WORKLOGPRO_HEADERS.index("User")
    assert_single_person(expected, (str(row[user_column]) for row in rows))


def _slug(text: str) -> str:
    """Nazwa bezpieczna dla systemu plików: ASCII, małe litery, myślniki.

    Polskie znaki rozkładamy przez NFKD i odrzucamy diakrytyki — nazwa pliku ma przeżyć
    przeniesienie na udział sieciowy i Linuksa bez niespodzianek z kodowaniem. Litery
    nierozkładalne (``ł``!) tłumaczymy wcześniej, inaczej NFKD wyciąłby je BEZ ŚLADU.
    """
    mapped = text.translate(_UNDECOMPOSABLE)
    folded = unicodedata.normalize("NFKD", mapped).encode("ascii", "ignore").decode("ascii")
    return _FILENAME_RE.sub("-", folded.lower()).strip("-") or "bez-nazwy"
