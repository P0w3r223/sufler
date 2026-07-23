"""Źródło commitów osoby z GitHuba (ADR 0036) — port ``CommitSource`` dla kart czasu.

Cienki adapter nad ``GithubReadPort.list_commits`` + ``map_github_commits`` (biała lista pól,
współdzielona z propozycją czasu ADR 0034). Filtruje po autorze (e-mail git z mapy tożsamości)
i po oknie tygodnia; klucze Jira wyłuskuje dopiero rdzeń (``issue_attribution``).

``github`` można wstrzyknąć (test na atrapie ``GithubReadPort``); w produkcji ``None`` → własny,
krótkotrwały ``httpx.Client`` + ``HttpxGithubClient`` na czas odczytu (przebieg jest wsadowy).
Import ``httpx``/klienta LENIWY (extra ``github``).

Ucięcie (``MAX_COMMITS_PER_FETCH``): przy >500 commitach jednego autora w oknie GitHub oddaje tylko
najnowsze, więc klucze z NAJSTARSZYCH dni przepadają, a te godziny (realne) trafiają na KOSZYK.
ŚWIADOMIE nie sygnalizujemy tego jak ``propose_worklog`` (ADR 0034) — arkusz przegląda człowiek,
a jeden autor z >500 commitami w tygodniu jest praktycznie niemożliwy; to uczciwa degradacja
(minuty nadal policzone), nie błędne dane.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from workmate.core.domain.worklog import Commit, map_github_commits

if TYPE_CHECKING:
    from workmate.core.ports.github import GithubReadPort


class GithubCommitSource:
    """``CommitSource`` czytający commity gałęzi domyślnej z GitHub REST (per autor, w oknie)."""

    def __init__(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        tz: ZoneInfo,
        api_base: str = "https://api.github.com",
        timeout: float = 30.0,
        github: GithubReadPort | None = None,
    ) -> None:
        self._token = token
        self._owner = owner
        self._repo = repo
        self._tz = tz
        self._api_base = api_base
        self._timeout = timeout
        self._github = github

    def commits_for(self, git_email: str, since: date, until: date) -> list[Commit]:
        if self._github is not None:
            return self._fetch(self._github, git_email, since, until)
        import httpx

        from workmate.adapters.outbound.github_api import HttpxGithubClient

        with httpx.Client(timeout=self._timeout) as client:
            github = HttpxGithubClient(client, self._token, api_base=self._api_base)
            return self._fetch(github, git_email, since, until)

    def _fetch(
        self, github: GithubReadPort, git_email: str, since: date, until: date
    ) -> list[Commit]:
        # Okno = te same lokalne północe co tydzień Shifts: [pon. 00:00, następny pon. 00:00).
        raw = github.list_commits(
            self._owner,
            self._repo,
            since=datetime.combine(since, time(), tzinfo=self._tz),
            until=datetime.combine(until, time(), tzinfo=self._tz),
            author=git_email,
        )
        return map_github_commits(raw)
