"""Złożone źródło godzin "shifts" (ADR 0036): Microsoft Shifts + commity + claude_summary.

Spełnia port ``HoursSource``, więc wchodzi w NIEZMIENIONY ``WeeklyTimesheetService``. Minuty to
REALNE godziny opublikowanych zmian (nie estymacja — ADR 0035 § dozwolone tylko liczby ze źródła,
które je zna). Klucze issue bierzemy z commitów danego dnia i ROZDZIELAMY na nie realne minuty
(to nie estymacja ilości — patrz ``issue_attribution``); dzień bez kluczy w całości na koszyk.
Komentarz to opis dnia z ``claude_summary`` (co osoba robiła), ten sam dla wszystkich zgłoszeń dnia.

Nieznane konto (zmiana byłego pracownika/gościa spoza mapy tożsamości) jest pomijane FAIL-CLOSED —
zgadywanie osoby wpisałoby czas do nikogo albo na cudze konto Jiry przy imporcie. Brak źródła
commitów/opisów lub brak ``git_email`` osoby → koszyk / pusty komentarz (degradacja, nie błąd).
"""

from __future__ import annotations

import logging
from datetime import date
from zoneinfo import ZoneInfo

from workmate.core.domain.issue_attribution import issue_keys_by_day, split_day_minutes
from workmate.core.domain.shift_hours import minutes_by_person_day
from workmate.core.domain.timesheet import Person, WorkEntry
from workmate.core.ports.timesheets import (
    AadIdentityLookup,
    CommitSource,
    ShiftSource,
    TaskSummarySource,
)

logger = logging.getLogger(__name__)


class ShiftsHoursSource:
    """``HoursSource``: godziny Shifts → zgłoszenia z commitów + opis dnia z claude_summary."""

    def __init__(
        self,
        shifts: ShiftSource,
        identities: AadIdentityLookup,
        *,
        fallback_issue: str,
        tz: ZoneInfo,
        commits: CommitSource | None = None,
        summaries: TaskSummarySource | None = None,
    ) -> None:
        self._shifts = shifts
        self._identities = identities
        self._fallback_issue = fallback_issue
        self._tz = tz
        self._commits = commits
        self._summaries = summaries

    def read(self, since: date, until: date) -> list[WorkEntry]:
        blocks = self._shifts.read_blocks()
        entries: list[WorkEntry] = []
        # Klucze i opisy per osoba pobieramy RAZ (po git_email) i cache'ujemy — jeden zaciąg
        # commitów/opisów pokrywa wszystkie dni tygodnia tej osoby.
        keys_cache: dict[str, dict[date, list[str]]] = {}
        comment_cache: dict[str, dict[date, str]] = {}
        for row in minutes_by_person_day(blocks, since=since, until=until, tz=self._tz):
            person = self._identities.resolve_by_aad_user_id(row.user_id)
            if person is None:
                logger.warning(
                    "Zmiana dla nieznanego konta %s (%s) — pomijam (fail-closed).",
                    row.user_id,
                    row.day,
                )
                continue
            day_keys = self._keys_for(person, since, until, keys_cache).get(row.day, [])
            comment = self._comment_for(person, since, until, comment_cache).get(row.day, "")
            for issue_key, share in split_day_minutes(
                row.minutes, day_keys, fallback_issue=self._fallback_issue
            ):
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

    def _keys_for(
        self,
        person: Person,
        since: date,
        until: date,
        cache: dict[str, dict[date, list[str]]],
    ) -> dict[date, list[str]]:
        """Klucze Jira per dzień z commitów osoby; brak źródła albo e-maila → ``{}`` (koszyk)."""
        if self._commits is None or not person.git_email:
            return {}
        if person.git_email not in cache:
            commits = self._commits.commits_for(person.git_email, since, until)
            cache[person.git_email] = issue_keys_by_day(commits, tz=self._tz)
        return cache[person.git_email]

    def _comment_for(
        self,
        person: Person,
        since: date,
        until: date,
        cache: dict[str, dict[date, str]],
    ) -> dict[date, str]:
        """Opis dnia per data z claude_summary; brak źródła albo e-maila → ``{}`` (pusty opis)."""
        if self._summaries is None or not person.git_email:
            return {}
        if person.git_email not in cache:
            comments = self._summaries.comments_by_day(person.git_email, since, until)
            cache[person.git_email] = comments
        return cache[person.git_email]
