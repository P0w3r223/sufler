"""Przypisanie REALNYCH godzin dnia (z Shifts) do zgłoszeń Jira z commitów (ADR 0036).

CZYSTA logika: bez I/O. To NIE estymacja (kontrast z ADR 0034) — ilość minut jest znana ze
Shifts; commity służą wyłącznie do ROZDZIAŁU tych minut na zgłoszenia. Dzień bez klucza w commitach
w całości trafia na koszykowe issue (``fallback_issue``), więc żaden wiersz arkusza nie zostaje bez
``issue_key`` (import WorklogPRO odrzuca wiersz bez klucza).

Podział minut między klucze jest RÓWNY z resztą do pierwszych kluczy — dokładnie jak
``worklog._split_minutes`` (sumy zgadzają się co do minuty, bez dryfu float). Nie mamy danych, by
ważyć inaczej, a przypisanie całości pierwszemu kluczowi zawyżałoby go systematycznie.
"""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from workmate.core.domain.worklog import Commit, extract_issue_keys, local_day


def issue_keys_by_day(commits: list[Commit], *, tz: ZoneInfo) -> dict[date, list[str]]:
    """Klucze Jira z commitów pogrupowane po LOKALNEJ dobie (bez powtórzeń, w kolejności)."""
    by_day: dict[date, list[str]] = {}
    for commit in commits:
        keys = extract_issue_keys(commit.message)
        if not keys:
            continue
        day = local_day(commit.authored_at, tz)
        bucket = by_day.setdefault(day, [])
        for key in keys:
            if key not in bucket:
                bucket.append(key)
    return by_day


def split_day_minutes(
    minutes: int, issue_keys: list[str], *, fallback_issue: str
) -> list[tuple[str, int]]:
    """Rozdziel ``minutes`` na zgłoszenia; brak kluczy → całość na ``fallback_issue``.

    Zwraca pary ``(issue_key, minutes)`` z dodatnimi minutami (udziały zerowe — gdy kluczy więcej
    niż minut — pomijamy, żeby nie tworzyć pustych wierszy). Powtórzone klucze traktujemy jak jeden.
    """
    if minutes <= 0:
        return []
    keys = _dedup(issue_keys)
    if not keys:
        return [(fallback_issue, minutes)]
    base, remainder = divmod(minutes, len(keys))
    result: list[tuple[str, int]] = []
    for index, key in enumerate(keys):
        share = base + (1 if index < remainder else 0)
        if share > 0:
            result.append((key, share))
    return result


def _dedup(keys: list[str]) -> list[str]:
    """Usuń powtórzenia zachowując kolejność (reszta z podziału ma iść do PIERWSZYCH kluczy)."""
    seen: dict[str, None] = {}
    for key in keys:
        seen.setdefault(key, None)
    return list(seen)
