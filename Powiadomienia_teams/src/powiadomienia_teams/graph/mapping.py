"""Mapowanie surowego JSON Microsoft Graph → model domenowy (czyste, bez sieci).

Wydzielone od klienta HTTP, żeby dało się je testować na utrwalonych odpowiedziach Graph.
Wszystkie mappery zwracają ``None`` dla wpisów, których nie da się bezpiecznie zinterpretować
(brak wymaganych pól, zła data, niepoprawny zakres) — warstwa wyżej je pomija.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from powiadomienia_teams.domain.models import (
    InvalidShift,
    InvalidTimeOff,
    Member,
    Shift,
    TimeOff,
)

_FRACTION = re.compile(r"\.(\d+)")
_UTC = timezone.utc


def parse_graph_datetime(value: str) -> datetime:
    """ISO 8601 z Graph (``…Z``) → tz-aware ``datetime`` (UTC).

    Znosi ułamek sekundy dłuższy niż 6 cyfr (Graph bywa 7-cyfrowy, a ``fromisoformat``
    poniżej Pythona 3.11 tego nie przyjmuje).
    """
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    v = _FRACTION.sub(lambda m: "." + m.group(1)[:6], v)
    return datetime.fromisoformat(v)


def to_graph_iso(dt: datetime) -> str:
    """tz-aware ``datetime`` → ISO 8601 UTC z sufiksem ``Z`` (format oczekiwany przez Graph).

    Odwrotność ``parse_graph_datetime``. Wspólny formatter dla klienta Graph (ciała żądań) i
    watermarku przypomnień — jedno źródło formatu, brak rozjazdu między adapterem a orkiestracją.
    """
    return dt.astimezone(_UTC).isoformat().replace("+00:00", "Z")


def member_from_json(raw: dict[str, Any]) -> Member | None:
    """Wpis ``/teams/{id}/members`` → ``Member`` (albo ``None`` bez userId/nazwy)."""
    user_id = raw.get("userId")
    display_name = raw.get("displayName")
    if not user_id or not display_name:
        return None
    email = raw.get("email")
    roles = raw.get("roles") or []
    return Member(
        user_id=str(user_id),
        display_name=str(display_name),
        email=(str(email) if email else None),
        roles=tuple(str(r) for r in roles),
    )


def shift_from_json(raw: dict[str, Any]) -> Shift | None:
    """Wpis ``/schedule/shifts`` → ``Shift``. Preferuje ``sharedShift`` (opublikowaną).

    ``None``, gdy brak userId, brak ciała zmiany, brak dat lub zakres jest niepoprawny.
    """
    user_id = raw.get("userId")
    if not user_id:
        return None

    body = raw.get("sharedShift")
    if body is None:
        body = raw.get("draftShift")
    if not body:
        return None

    start_raw = body.get("startDateTime")
    end_raw = body.get("endDateTime")
    if not start_raw or not end_raw:
        return None

    try:
        start = parse_graph_datetime(start_raw)
        end = parse_graph_datetime(end_raw)
        return Shift(
            user_id=str(user_id),
            start=start,
            end=end,
            scheduling_group_id=raw.get("schedulingGroupId"),
            theme=body.get("theme"),
        )
    except (ValueError, InvalidShift):
        return None


def time_off_from_json(raw: dict[str, Any]) -> TimeOff | None:
    """Wpis ``/schedule/timesOff`` → ``TimeOff``. Preferuje ``sharedTimeOff`` (opublikowany).

    Lustrzane wobec ``shift_from_json``: ``None``, gdy brak userId, ciała wpisu, dat, powodu lub
    gdy zakres jest niepoprawny.
    """
    user_id = raw.get("userId")
    if not user_id:
        return None

    body = raw.get("sharedTimeOff")
    if body is None:
        body = raw.get("draftTimeOff")
    if not body:
        return None

    start_raw = body.get("startDateTime")
    end_raw = body.get("endDateTime")
    reason_id = body.get("timeOffReasonId")
    if not start_raw or not end_raw or not reason_id:
        return None

    try:
        return TimeOff(
            user_id=str(user_id),
            start=parse_graph_datetime(start_raw),
            end=parse_graph_datetime(end_raw),
            reason_id=str(reason_id),
        )
    except (ValueError, InvalidTimeOff):
        return None
