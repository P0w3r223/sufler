"""Testy ``WorklogService`` (ADR 0034) — strażniki zapisu i propozycja bez mutacji.

Atrapy portów są STRUKTURALNE (bez dziedziczenia), jak reszta testów rdzenia. Zegar wstrzykujemy,
więc granice dat są deterministyczne. Atrapa worklogu ZAPAMIĘTUJE wywołania — dzięki temu można
asertować nie tylko „poleciał wyjątek", ale też „nic nie dotarło do Jiry".

Konta jak w środowisku BIAP: Piotr (właściciel tokenu) i Mikołaj (druga osoba, ścieżka cross-user).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pytest

from workmate.core.application.worklog import WorklogService
from workmate.core.application.worklog_author import SelfAuthorStrategy
from workmate.core.domain.worklog import SessionPolicy
from workmate.core.errors import WriteError

_PIOTR = "712020:c0ffee00-0000-4000-8000-000000000008"
_MIKOLAJ = "712020:c0ffee00-0000-4000-8000-000000000018"
_TODAY = date(2026, 7, 20)
_YESTERDAY = date(2026, 7, 19)


def _now() -> datetime:
    return datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)


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
        self.calls.append({"owner": owner, "repo": repo, "author": author, "since": since})
        return self.commits


class _FakeWorklogs:
    """Atrapa portu worklogu — rejestruje zapisy i oddaje zadane istniejące wpisy."""

    def __init__(self, existing: list[dict[str, Any]] | None = None) -> None:
        self.existing = existing or []
        self.written: list[dict[str, Any]] = []

    def add_worklog(
        self,
        issue_key: str,
        *,
        time_spent_seconds: int,
        started: str,
        comment: str = "",
        on_behalf_of: str = "",
    ) -> dict[str, Any]:
        self.written.append(
            {
                "issue_key": issue_key,
                "time_spent_seconds": time_spent_seconds,
                "started": started,
                "comment": comment,
                "on_behalf_of": on_behalf_of,
            }
        )
        return {
            "id": "10501",
            "url": f"https://example.atlassian.net/browse/{issue_key}",
            "created": "2026-07-20T10:00:00.000+0200",
            "author_account_id": _PIOTR,
            "requested_author": on_behalf_of,
        }

    def read_worklogs(self, issue_key: str, *, max_results: int = 100) -> list[dict[str, Any]]:
        return self.existing


class _FakeEvents:
    def __init__(self) -> None:
        self.ingested: list[Any] = []

    def ingest(self, event: Any) -> None:
        self.ingested.append(event)


def _commit(sha: str, when: str, message: str = "WT-1 praca") -> dict[str, Any]:
    return {
        "sha": sha,
        "html_url": f"https://github.com/x/y/commit/{sha}",
        "author": {"login": "P0w3r223"},
        "commit": {"message": message, "author": {"date": when, "email": "piotr@example.com"}},
    }


def _service(
    *,
    worklogs: _FakeWorklogs | None = None,
    github: _FakeGithub | None = None,
    allow_on_behalf: bool = False,
    duplicate_guard: bool = True,
    events: _FakeEvents | None = None,
    max_backdate_days: int = 14,
) -> WorklogService:
    return WorklogService(
        github or _FakeGithub(),  # type: ignore[arg-type]
        worklogs,  # type: ignore[arg-type]
        owner="BIAP-Inteligentne-Technologie",
        repo="PIWorkmate",
        project="WT",
        author_strategy=SelfAuthorStrategy(_PIOTR),
        policy=SessionPolicy(tz_offset_minutes=120),
        allow_on_behalf=allow_on_behalf,
        duplicate_guard=duplicate_guard,
        max_backdate_days=max_backdate_days,
        events=events,  # type: ignore[arg-type]
        now=_now,
    )


# --- bramka -----------------------------------------------------------------------


def test_write_is_refused_when_gate_is_off() -> None:
    """Brak portu = brak zdolności STRUKTURALNIE, nie przez flagę do zapomnienia."""
    with pytest.raises(WriteError, match="ENABLE_WORKLOG"):
        _service(worklogs=None).log_jira_worklog("WT-1", 2.0, _TODAY)


# --- strażnik klucza i projektu ---------------------------------------------------


def test_key_from_another_project_is_rejected_before_touching_jira() -> None:
    worklogs = _FakeWorklogs()
    with pytest.raises(WriteError, match="spoza skonfigurowanego"):
        _service(worklogs=worklogs).log_jira_worklog("OPS-1", 2.0, _TODAY)
    assert worklogs.written == []


def test_path_traversal_key_is_rejected_before_touching_jira() -> None:
    """``WT-1/../OPS-1`` przeszłoby test prefiksu — dlatego walidujemy PEŁNY kształt klucza."""
    worklogs = _FakeWorklogs()
    with pytest.raises(WriteError, match="nie jest poprawnym kluczem"):
        _service(worklogs=worklogs).log_jira_worklog("WT-1/../OPS-1", 2.0, _TODAY)
    assert worklogs.written == []


def test_control_characters_in_comment_are_rejected() -> None:
    worklogs = _FakeWorklogs()
    with pytest.raises(WriteError, match="znak sterujący"):
        _service(worklogs=worklogs).log_jira_worklog("WT-1", 2.0, _TODAY, comment="zły\x00wpis")
    assert worklogs.written == []


# --- zakres godzin ----------------------------------------------------------------


@pytest.mark.parametrize("hours", [0, -1.5])
def test_non_positive_hours_are_rejected(hours: float) -> None:
    with pytest.raises(WriteError, match="nie jest dodatnie"):
        _service(worklogs=_FakeWorklogs()).log_jira_worklog("WT-1", hours, _TODAY)


def test_hours_above_entry_limit_are_rejected() -> None:
    with pytest.raises(WriteError, match="przekracza limit"):
        _service(worklogs=_FakeWorklogs()).log_jira_worklog("WT-1", 99.0, _TODAY)


def test_sub_minute_entry_is_rejected_with_a_clear_message() -> None:
    """Jira odrzuca wpisy krótsze niż minuta — łapiemy to u siebie, z czytelnym powodem."""
    with pytest.raises(WriteError, match="mniej niż minuta"):
        _service(worklogs=_FakeWorklogs()).log_jira_worklog("WT-1", 0.005, _TODAY)


def test_hours_are_converted_to_seconds() -> None:
    worklogs = _FakeWorklogs()
    _service(worklogs=worklogs).log_jira_worklog("WT-1", 1.5, _TODAY)
    assert worklogs.written[0]["time_spent_seconds"] == 5400


# --- zakres dat -------------------------------------------------------------------


def test_future_date_is_rejected() -> None:
    with pytest.raises(WriteError, match="w przyszłości"):
        _service(worklogs=_FakeWorklogs()).log_jira_worklog("WT-1", 2.0, date(2026, 7, 25))


def test_date_older_than_backdate_window_is_rejected() -> None:
    with pytest.raises(WriteError, match="starsza niż"):
        _service(worklogs=_FakeWorklogs()).log_jira_worklog("WT-1", 2.0, date(2026, 1, 5))


def test_started_carries_the_configured_timezone_offset() -> None:
    worklogs = _FakeWorklogs()
    _service(worklogs=worklogs).log_jira_worklog("WT-1", 2.0, _YESTERDAY)
    assert worklogs.written[0]["started"].startswith("2026-07-19T12:00:00.000+0200")


# --- ścieżka cross-user (Piotr → Mikołaj) ------------------------------------------


def test_on_behalf_of_is_refused_while_its_own_gate_is_off() -> None:
    """Cross-user ma DRUGĄ bramkę — włączenie ewidencji samo w sobie go nie otwiera."""
    worklogs = _FakeWorklogs()
    with pytest.raises(WriteError, match="ALLOW_ON_BEHALF"):
        _service(worklogs=worklogs, allow_on_behalf=False).log_jira_worklog(
            "WT-1", 2.0, _TODAY, on_behalf_of=_MIKOLAJ
        )
    assert worklogs.written == []


def test_on_behalf_of_passes_account_and_annotates_comment() -> None:
    worklogs = _FakeWorklogs()
    _service(worklogs=worklogs, allow_on_behalf=True).log_jira_worklog(
        "WT-1", 2.0, _TODAY, comment="przegląd kodu", on_behalf_of=_MIKOLAJ, display_name="Mikołaj"
    )
    written = worklogs.written[0]
    assert written["on_behalf_of"] == _MIKOLAJ
    assert written["comment"].startswith("w imieniu: Mikołaj")
    assert "przegląd kodu" in written["comment"]


def test_on_behalf_of_result_reports_honest_attribution() -> None:
    """Wynik musi mówić PRAWDĘ: autorem w Jirze jest Piotr, nie Mikołaj."""
    result = _service(worklogs=_FakeWorklogs(), allow_on_behalf=True).log_jira_worklog(
        "WT-1", 2.0, _TODAY, on_behalf_of=_MIKOLAJ, display_name="Mikołaj"
    )
    assert result["author"] == _PIOTR
    assert result["on_behalf_of"] == _MIKOLAJ
    assert "konto tokenu" in result["note"]


def test_malformed_account_id_is_rejected() -> None:
    worklogs = _FakeWorklogs()
    with pytest.raises(WriteError, match="accountId"):
        _service(worklogs=worklogs, allow_on_behalf=True).log_jira_worklog(
            "WT-1", 2.0, _TODAY, on_behalf_of="Mikołaj z księgowości!"
        )
    assert worklogs.written == []


def test_own_entry_carries_no_annotation() -> None:
    worklogs = _FakeWorklogs()
    _service(worklogs=worklogs).log_jira_worklog("WT-1", 2.0, _TODAY, comment="moja praca")
    assert worklogs.written[0]["comment"] == "moja praca"


# --- strażnik duplikatów ----------------------------------------------------------


def test_duplicate_entry_on_the_same_day_is_refused() -> None:
    """Bez usuwania wpisów duplikat jest nieusuwalny — wolimy odmówić."""
    existing = [{"id": "1", "author_account_id": _PIOTR, "started": "2026-07-20T09:00:00.000+0200"}]
    worklogs = _FakeWorklogs(existing=existing)
    with pytest.raises(WriteError, match="ma już czas zapisany"):
        _service(worklogs=worklogs).log_jira_worklog("WT-1", 2.0, _TODAY)
    assert worklogs.written == []


def test_entry_by_another_account_does_not_block() -> None:
    existing = [
        {"id": "1", "author_account_id": _MIKOLAJ, "started": "2026-07-20T09:00:00.000+0200"}
    ]
    _service(worklogs=(worklogs := _FakeWorklogs(existing=existing))).log_jira_worklog(
        "WT-1", 2.0, _TODAY
    )
    assert len(worklogs.written) == 1


def test_entry_on_another_day_does_not_block() -> None:
    existing = [{"id": "1", "author_account_id": _PIOTR, "started": "2026-07-14T09:00:00.000+0200"}]
    _service(worklogs=(worklogs := _FakeWorklogs(existing=existing))).log_jira_worklog(
        "WT-1", 2.0, _TODAY
    )
    assert len(worklogs.written) == 1


def test_duplicate_guard_can_be_disabled() -> None:
    existing = [{"id": "1", "author_account_id": _PIOTR, "started": "2026-07-20T09:00:00.000+0200"}]
    worklogs = _FakeWorklogs(existing=existing)
    _service(worklogs=worklogs, duplicate_guard=False).log_jira_worklog("WT-1", 2.0, _TODAY)
    assert len(worklogs.written) == 1


# --- echo zdarzenia ---------------------------------------------------------------


def test_successful_write_echoes_a_teams_event() -> None:
    """Echo ``source="teams"`` zamyka pętlę: notifier wypycha tylko ``source="jira"``."""
    events = _FakeEvents()
    _service(worklogs=_FakeWorklogs(), events=events).log_jira_worklog("WT-1", 2.0, _TODAY)
    event = events.ingested[0]
    assert event.source == "teams"
    assert event.kind == "jira_worklog"
    assert event.external_id == "WT-1:10501"


def test_write_without_event_store_still_succeeds() -> None:
    result = _service(worklogs=_FakeWorklogs(), events=None).log_jira_worklog("WT-1", 2.0, _TODAY)
    assert result["logged"] is True


# --- propozycja (ODCZYT) ----------------------------------------------------------


def test_proposal_performs_no_write_calls() -> None:
    """Inwariant kroku 1: propozycja NIGDY nie dotyka Jiry."""
    worklogs = _FakeWorklogs()
    github = _FakeGithub([_commit("a", "2026-07-15T09:12:00Z")])
    proposal = _service(worklogs=worklogs, github=github).propose_worklog(
        date(2026, 7, 13), date(2026, 7, 19)
    )
    assert worklogs.written == []
    assert proposal.commit_count == 1


def test_proposal_maps_commits_through_the_whitelist() -> None:
    github = _FakeGithub([_commit("abc", "2026-07-15T09:12:00Z", "WT-7 refaktor")])
    proposal = _service(worklogs=None, github=github).propose_worklog(
        date(2026, 7, 13), date(2026, 7, 19)
    )
    assert proposal.sessions[0].shas == ("abc",)
    assert proposal.by_issue[0].issue_key == "WT-7"


def test_proposal_skips_commits_without_a_usable_timestamp() -> None:
    """Jeden dziwny commit nie może wywrócić raportu — pomijamy go, nie zgadujemy daty."""
    broken = {"sha": "zzz", "commit": {"message": "WT-1", "author": {"date": "kiedyś"}}}
    github = _FakeGithub([broken, _commit("ok", "2026-07-15T09:12:00Z")])
    proposal = _service(worklogs=None, github=github).propose_worklog(
        date(2026, 7, 13), date(2026, 7, 19)
    )
    assert proposal.commit_count == 1


def test_proposal_forwards_author_filter_to_github() -> None:
    github = _FakeGithub()
    _service(worklogs=None, github=github).propose_worklog(
        date(2026, 7, 13), date(2026, 7, 19), author="P0w3r223"
    )
    assert github.calls[0]["author"] == "P0w3r223"


def test_proposal_rejects_inverted_range() -> None:
    with pytest.raises(WriteError, match="wcześniej niż"):
        _service(worklogs=None).propose_worklog(date(2026, 7, 19), date(2026, 7, 13))


def test_proposal_rejects_range_beyond_limit() -> None:
    with pytest.raises(WriteError, match="przekracza limit"):
        _service(worklogs=None).propose_worklog(date(2026, 1, 1), date(2026, 7, 19))


def test_proposal_rejects_future_window() -> None:
    with pytest.raises(WriteError, match="w przyszłości"):
        _service(worklogs=None).propose_worklog(date(2026, 8, 1), date(2026, 8, 5))
