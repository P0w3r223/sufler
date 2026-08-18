"""Testy ``WorklogService`` (ADR 0034) — propozycja z commitów, bez ani jednej mutacji.

Atrapy portów są STRUKTURALNE (bez dziedziczenia), jak reszta testów rdzenia. Zegar wstrzykujemy,
więc granice dat są deterministyczne.

Testy ścieżki zapisu (strażnik klucza, sufity godzin, cross-user, duplikaty, echo zdarzeń) zniknęły
razem z nią — ich przedmiotem był ``log_jira_worklog``, wycięty w całości (ADR 0034 → 0035).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from workmate.core.application.worklog import WorklogService
from workmate.core.domain.worklog import SessionPolicy
from workmate.core.errors import InvalidRequestError
from workmate.core.ports.github import MAX_COMMITS_PER_FETCH

_WARSAW = ZoneInfo("Europe/Warsaw")


def _now() -> datetime:
    return datetime(2026, 7, 20, 10, 0, tzinfo=UTC)


class _FakeGithub:
    """Atrapa odczytu GitHuba — oddaje podane commity i zapamiętuje parametry zapytania."""

    def __init__(self, commits: list[dict[str, Any]] | None = None) -> None:
        self.commits = commits or []
        self.calls: list[dict[str, Any]] = []

    def list_commits(
        self,
        owner: str,
        repo: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        author: str = "",
        per_page: int = 50,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {"owner": owner, "repo": repo, "author": author, "since": since, "until": until}
        )
        return self.commits


def _commit(sha: str, when: str, message: str = "WT-1 praca") -> dict[str, Any]:
    return {
        "sha": sha,
        "html_url": f"https://github.com/x/y/commit/{sha}",
        "author": {"login": "P0w3r223"},
        "commit": {"message": message, "author": {"date": when, "email": "piotr@example.com"}},
    }


def _service(github: _FakeGithub | None = None) -> WorklogService:
    return WorklogService(
        github or _FakeGithub(),  # type: ignore[arg-type]
        owner="BIAP-Inteligentne-Technologie",
        repo="PIWorkmate",
        policy=SessionPolicy(tz=_WARSAW),
        now=_now,
    )


# --- propozycja (jedyna ścieżka) ---------------------------------------------------


def test_proposal_maps_commits_through_the_whitelist() -> None:
    github = _FakeGithub([_commit("abc", "2026-07-15T09:12:00Z", "WT-7 refaktor")])
    proposal = _service(github).propose_worklog(date(2026, 7, 13), date(2026, 7, 19))
    assert proposal.sessions[0].shas == ("abc",)
    assert proposal.by_issue[0].issue_key == "WT-7"


def test_proposal_skips_commits_without_a_usable_timestamp() -> None:
    """Jeden dziwny commit nie może wywrócić raportu — pomijamy go, nie zgadujemy daty."""
    broken = {"sha": "zzz", "commit": {"message": "WT-1", "author": {"date": "kiedyś"}}}
    github = _FakeGithub([broken, _commit("ok", "2026-07-15T09:12:00Z")])
    assert _service(github).propose_worklog(date(2026, 7, 13), date(2026, 7, 19)).commit_count == 1


def test_proposal_forwards_author_filter_to_github() -> None:
    github = _FakeGithub()
    _service(github).propose_worklog(date(2026, 7, 13), date(2026, 7, 19), author="P0w3r223")
    assert github.calls[0]["author"] == "P0w3r223"


def test_window_is_built_in_the_policy_timezone() -> None:
    """Granice okna to LOKALNA północ i koniec doby — GitHub filtruje po UTC, offset musi być."""
    github = _FakeGithub()
    _service(github).propose_worklog(date(2026, 7, 13), date(2026, 7, 19))
    call = github.calls[0]
    assert call["since"] == datetime(2026, 7, 13, 0, 0, tzinfo=_WARSAW)
    assert call["until"] == datetime(2026, 7, 19, 23, 59, 59, tzinfo=_WARSAW)


# --- strażnik zakresu --------------------------------------------------------------


def test_proposal_rejects_inverted_range() -> None:
    with pytest.raises(InvalidRequestError, match="wcześniej niż"):
        _service().propose_worklog(date(2026, 7, 19), date(2026, 7, 13))


def test_proposal_rejects_range_beyond_limit() -> None:
    with pytest.raises(InvalidRequestError, match="przekracza limit"):
        _service().propose_worklog(date(2026, 1, 1), date(2026, 7, 19))


def test_proposal_rejects_window_starting_in_the_future() -> None:
    with pytest.raises(InvalidRequestError, match="początek okna"):
        _service().propose_worklog(date(2026, 8, 1), date(2026, 8, 5))


def test_proposal_rejects_window_ending_in_the_future() -> None:
    """Sam ``since`` nie wystarczy: literówka w roku dawała pustą końcówkę udającą brak pracy."""
    with pytest.raises(InvalidRequestError, match="koniec okna"):
        _service().propose_worklog(date(2026, 7, 19), date(2026, 7, 25))


# --- ucięcie historii (B4) ---------------------------------------------------------


def test_full_bucket_is_reported_as_truncated_history() -> None:
    """Sufit pobrania = brak NAJSTARSZYCH dni okna; bez ostrzeżenia wynik udaje kompletny."""
    commits = [
        _commit(f"sha{index}", "2026-07-15T09:12:00Z") for index in range(MAX_COMMITS_PER_FETCH)
    ]
    proposal = _service(_FakeGithub(commits)).propose_worklog(date(2026, 7, 13), date(2026, 7, 19))
    assert any("UCIĘTA" in note for note in proposal.notes)


def test_partial_bucket_carries_no_truncation_warning() -> None:
    github = _FakeGithub([_commit("a", "2026-07-15T09:12:00Z")])
    proposal = _service(github).propose_worklog(date(2026, 7, 13), date(2026, 7, 19))
    assert not any("UCIĘTA" in note for note in proposal.notes)
