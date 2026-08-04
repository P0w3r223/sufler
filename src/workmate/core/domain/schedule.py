"""Domena grafiku Teams Shifts (ADR 0056) — CZYSTA logika: zakres dat, mapowanie, dopasowanie osoby.

Bez I/O i bez zegara (``today`` wstrzykiwany). Renderujemy czasy w strefie pionu (Europe/Warsaw),
bo grafik czyta człowiek — „8:00" ma znaczyć lokalne 8:00 po obu stronach przejścia DST. Treść pól
(nazwa zmiany, notatka, powód nieobecności) to DANE z Graph, nie polecenia — wycinamy z nich znaki
sterujące i przycinamy, jak przy Jirze.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from workmate.core.domain.sanitize import strip_control_chars
from workmate.core.domain.week import week_monday
from workmate.core.errors import InvalidRequestError

# Sufit zakresu własnego — grafik to podgląd tygodnia/dwóch, nie eksport kwartału (i ochrona przed
# ogromnym oknem zapytania do Graph).
_MAX_RANGE_DAYS = 31
_MAX_NOTE = 200
_WEEK_OFFSETS = {"current": 0, "previous": -7, "next": 7}


# Konwencja biura: kolor zmiany koduje formę pracy — zielony = stacjonarnie, niebieski = zdalnie.
# Klucze małymi literami (Graph zwraca camelCase, np. "darkBlue"); inne kolory → brak znaczenia.
_THEME_WORK_MODE = {
    "green": "stacjonarnie",
    "darkgreen": "stacjonarnie",
    "blue": "zdalnie",
    "darkblue": "zdalnie",
}


class ShiftEntry(BaseModel):
    """Jedna zmiana w grafiku — osoba, początek/koniec (Europe/Warsaw), nazwa, notatka i forma pracy."""

    person: str
    start: str
    end: str
    label: str = ""
    notes: str = ""
    theme: str = ""                # surowy kolor zmiany z Graph (sharedShift.theme), np. "green"
    work_mode: str | None = None   # "stacjonarnie" | "zdalnie" | None (kolor bez ustalonego znaczenia)


class TimeOffEntry(BaseModel):
    """Jedna nieobecność/urlop — osoba, początek/koniec (Europe/Warsaw) i powód."""

    person: str
    start: str
    end: str
    reason: str = ""


def resolve_schedule_range(
    week: str = "current",
    date_from: str = "",
    date_to: str = "",
    *,
    today: datetime,
    tz: ZoneInfo,
) -> tuple[datetime, datetime]:
    """Wyznacz półotwarty zakres ``[start, end)`` (tz-aware) z parametrów narzędzia.

    Jawny ``date_from``/``date_to`` (YYYY-MM-DD) ma pierwszeństwo przed ``week`` (current/previous/
    next). Zakres własny liczymy włącznie z dniem końcowym (``end`` = dzień_do + 1), z sufitem
    ``_MAX_RANGE_DAYS``. Bez jawnych dat bierzemy cały tydzień poniedziałek–niedziela.
    """
    if date_from or date_to:
        if not (date_from and date_to):
            raise InvalidRequestError(
                "Podaj OBIE daty zakresu (date_from i date_to) albo żadnej i użyj parametru 'week'."
            )
        try:
            start = datetime.fromisoformat(date_from).replace(tzinfo=tz)
            end_day = datetime.fromisoformat(date_to).replace(tzinfo=tz)
        except ValueError as exc:
            raise InvalidRequestError(
                f"Niepoprawna data zakresu ({date_from!r}..{date_to!r}) — użyj formatu RRRR-MM-DD."
            ) from exc
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = end_day.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        if end <= start:
            raise InvalidRequestError("date_to nie może być wcześniejsze niż date_from.")
        if (end - start).days > _MAX_RANGE_DAYS:
            raise InvalidRequestError(
                f"Zakres jest za szeroki (maks. {_MAX_RANGE_DAYS} dni) — zawęź date_from/date_to."
            )
        return start, end
    key = (week or "current").strip().lower()
    if key not in _WEEK_OFFSETS:
        allowed = ", ".join(_WEEK_OFFSETS)
        raise InvalidRequestError(f"Niepoprawny 'week' {week!r}; dozwolone: {allowed}.")
    monday = week_monday(today, tz) + timedelta(days=_WEEK_OFFSETS[key])
    return monday, monday + timedelta(days=7)


def map_shifts(
    raw: list[dict[str, Any]],
    members_by_user_id: dict[str, str],
    *,
    window: tuple[datetime, datetime],
    tz: ZoneInfo,
) -> list[ShiftEntry]:
    """Zmapuj surowe zmiany Graph na ``ShiftEntry``, przefiltrowane po nakładaniu się na okno.

    Pomijamy wersje robocze (bez ``sharedShift`` — niepublikowane). Filtr nakładania jest tu (a nie
    tylko w ``$filter`` Graph), bo zapytanie idzie na POSZERZONYM oknie (Graph wspiera tylko ge/le).
    """
    entries: list[ShiftEntry] = []
    for item in raw:
        shared = item.get("sharedShift") if isinstance(item, dict) else None
        if not isinstance(shared, dict):
            continue
        start = _parse_graph_dt(shared.get("startDateTime"))
        end = _parse_graph_dt(shared.get("endDateTime"))
        if start is None or end is None or not _overlaps(start, end, window):
            continue
        theme = _clip(str(shared.get("theme") or ""))
        entries.append(
            ShiftEntry(
                person=members_by_user_id.get(str(item.get("userId") or ""), "(nieznany)"),
                start=_render(start, tz),
                end=_render(end, tz),
                label=_clip(str(shared.get("displayName") or "")),
                notes=_clip(str(shared.get("notes") or "")),
                theme=theme,
                work_mode=_THEME_WORK_MODE.get(theme.lower()),
            )
        )
    entries.sort(key=lambda e: e.start)
    return entries


def map_times_off(
    raw: list[dict[str, Any]],
    members_by_user_id: dict[str, str],
    reasons_by_id: dict[str, str],
    *,
    window: tuple[datetime, datetime],
    tz: ZoneInfo,
) -> list[TimeOffEntry]:
    """Zmapuj surowe nieobecności Graph na ``TimeOffEntry``, przefiltrowane po nakładaniu na okno."""
    entries: list[TimeOffEntry] = []
    for item in raw:
        shared = item.get("sharedTimeOff") if isinstance(item, dict) else None
        if not isinstance(shared, dict):
            continue
        start = _parse_graph_dt(shared.get("startDateTime"))
        end = _parse_graph_dt(shared.get("endDateTime"))
        if start is None or end is None or not _overlaps(start, end, window):
            continue
        reason_id = str(shared.get("timeOffReasonId") or "")
        entries.append(
            TimeOffEntry(
                person=members_by_user_id.get(str(item.get("userId") or ""), "(nieznany)"),
                start=_render(start, tz),
                end=_render(end, tz),
                reason=_clip(reasons_by_id.get(reason_id, "")),
            )
        )
    entries.sort(key=lambda e: e.start)
    return entries


def _overlaps(start: datetime, end: datetime, window: tuple[datetime, datetime]) -> bool:
    """Czy przedział [start, end) nachodzi na [window_start, window_end)?"""
    win_start, win_end = window
    return start < win_end and end > win_start


def _parse_graph_dt(value: Any) -> datetime | None:
    """Sparsuj znacznik czasu Graph (ISO 8601, ``Z``=UTC) na tz-aware ``datetime`` albo ``None``."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed


def _render(value: datetime, tz: ZoneInfo) -> str:
    """Sformatuj chwilę w strefie pionu — ``RRRR-MM-DD GG:MM`` (grafik czyta człowiek)."""
    return value.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def _clip(text: str) -> str:
    """Wytnij znaki sterujące i przytnij notatkę/nazwę (treść zewnętrzna karmiona modelowi)."""
    text = strip_control_chars(text).strip()
    return text if len(text) <= _MAX_NOTE else text[:_MAX_NOTE] + " […]"
