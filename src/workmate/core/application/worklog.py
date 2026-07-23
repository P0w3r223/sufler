"""Propozycja ewidencji czasu z historii commitów (ADR 0034, część odczytowa).

Zdolność jest WYŁĄCZNIE odczytowa: czyta commity z GitHuba i zwraca ESTYMACJĘ godzin. Nic nie
mutuje — ani w GitHubie, ani w Jirze.

Ścieżka zapisu (``log_jira_worklog``, strategie autorstwa, strażnik duplikatów) została USUNIĘTA.
Istniała, żeby obejść mur: Jira przypisuje worklog kontu tokenu i ignoruje pole ``author``, więc
czas zapisany „w imieniu" kogoś innego trafiał do raportów jako czas konta usługowego. ADR 0035
usunął sam mur — godziny idą arkuszem WorklogPRO, który importuje pracownik, więc autor jest
prawdziwy. Obejście straciło przedmiot, a wraz z nim zniknęła jedyna w tym module mutacja.

Klucze Jira wyłuskujemy z treści commitów regexem — to jedyny związek z Jirą, więc zdolność
wchodzi po stronie GitHuba (``GithubSettings``) i nie ma własnej bramki: odczyt jest domyślny,
bramkujemy zapis (ADR 0006).

Serwis nie woła zegara wprost — ``now`` jest wstrzykiwane, dzięki czemu testy granic dat są
deterministyczne.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timezone
from typing import TYPE_CHECKING

from workmate.core.domain.guards import bounded
from workmate.core.domain.sanitize import reject_dangerous_content
from workmate.core.domain.worklog import (
    SessionPolicy,
    WorklogProposal,
    build_proposal,
    local_day,
    map_github_commits,
)
from workmate.core.errors import InvalidRequestError
from workmate.core.ports.github import MAX_COMMITS_PER_FETCH

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo

    from workmate.core.ports.github import GithubReadPort

_MAX_AUTHOR = 255


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorklogService:
    """Propozycja czasu pracy z commitów — jedna metoda, zero mutacji."""

    def __init__(
        self,
        github: GithubReadPort,
        *,
        owner: str,
        repo: str,
        policy: SessionPolicy | None = None,
        max_range_days: int = 31,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._github = github
        self._owner = owner
        self._repo = repo
        self._policy = policy or SessionPolicy()
        self._max_range_days = max_range_days
        self._now = now

    def propose_worklog(self, since: date, until: date, author: str = "") -> WorklogProposal:
        """Zbuduj propozycję ewidencji z commitów w oknie ``since``..``until``. Nic nie zapisuje.

        ``author`` (login GitHub albo e-mail) zawęża do jednej osoby; pusty bierze wszystkich
        z repozytorium. Zakres jest ograniczony, żeby jedno wywołanie nie zaciągnęło całej
        historii repo (koszt limitu API i bezużytecznie wielka odpowiedź).
        """
        self._require_range(since, until)
        reject_dangerous_content(author)
        author = bounded(author.strip(), _MAX_AUTHOR, "autor commitów")
        raw = self._github.list_commits(
            self._owner,
            self._repo,
            since=_start_of_day(since, self._policy.tz),
            until=_end_of_day(until, self._policy.tz),
            author=author,
        )
        return build_proposal(
            map_github_commits(raw),
            since=since,
            until=until,
            policy=self._policy,
            author=author,
            # Port oddaje najwyżej ``MAX_COMMITS_PER_FETCH`` pozycji, licząc OD NAJNOWSZYCH.
            # Pełne wiadro znaczy więc, że najstarsze dni okna wypadły — propozycja jest
            # zaniżona i musi to powiedzieć wprost, zamiast wyglądać na kompletną.
            truncated=len(raw) >= MAX_COMMITS_PER_FETCH,
        )

    def _require_range(self, since: date, until: date) -> None:
        """Okno musi być poprawne, skończone i nie sięgać archeologii repozytorium."""
        if until < since:
            raise InvalidRequestError(f"zakres odrzucony: {until} jest wcześniej niż {since}.")
        span_days = (until - since).days + 1
        if span_days > self._max_range_days:
            raise InvalidRequestError(
                f"zakres odrzucony: {span_days} dni przekracza limit {self._max_range_days} "
                "— podziel zapytanie na krótsze okna."
            )
        # Obie granice sprawdzamy wobec dziś. Sam ``since`` nie wystarczy: okno kończące się
        # w przyszłości to zwykle literówka w roku, a odpowiedź (pusta końcówka) wyglądałaby
        # na brak pracy zamiast na złe zapytanie.
        today = self._today()
        for label, day in (("początek", since), ("koniec", until)):
            if day > today:
                raise InvalidRequestError(
                    f"zakres odrzucony: {label} okna ({day}) jest w przyszłości."
                )

    def _today(self) -> date:
        """Dzisiaj w strefie z polityki — granice dat liczymy tak samo jak doby sesji."""
        return local_day(self._now(), self._policy.tz)


def _start_of_day(day: date, tz: ZoneInfo) -> datetime:
    """Początek doby lokalnej jako aware ``datetime`` (GitHub filtruje po UTC).

    Wall-clock składamy przez ``combine(..., tzinfo=tz)``, więc „północ" jest lokalna po obu
    stronach zmiany czasu — nie stałym offsetem od UTC.
    """
    return datetime.combine(day, time(), tzinfo=tz)


def _end_of_day(day: date, tz: ZoneInfo) -> datetime:
    """Koniec doby lokalnej — okno jest DOMKNIĘTE, więc ``until`` też się liczy."""
    return datetime.combine(day, time(23, 59, 59), tzinfo=tz)
