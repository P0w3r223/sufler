"""Źródło commitów z GitHuba: filtr autora + okna, mapowanie białą listą (ADR 0036)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from workmate.adapters.outbound.github_commit_source import GithubCommitSource

WARSAW = ZoneInfo("Europe/Warsaw")


class FakeGithub:
    """Atrapa ``GithubReadPort`` — zwraca zadane surowe JSON-y i zapamiętuje argumenty wywołania."""

    def __init__(self, raw: list[dict[str, Any]]) -> None:
        self._raw = raw
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
            {"owner": owner, "repo": repo, "since": since, "until": until, "author": author}
        )
        return self._raw


def _raw(sha: str, message: str, when: str, email: str = "me@x.pl") -> dict[str, Any]:
    return {
        "sha": sha,
        "html_url": f"https://github.com/o/r/commit/{sha}",
        "commit": {"message": message, "author": {"date": when, "email": email}},
        "author": {"login": "me"},
    }


def _source(raw: list[dict[str, Any]]) -> tuple[GithubCommitSource, FakeGithub]:
    fake = FakeGithub(raw)
    source = GithubCommitSource(token="t", owner="O", repo="R", tz=WARSAW, github=fake)
    return source, fake


def test_maps_raw_commits_and_passes_author_and_window() -> None:
    source, fake = _source([_raw("abc", "WT-1 fix\nszczegóły", "2026-07-15T08:00:00Z")])
    commits = source.commits_for("me@x.pl", date(2026, 7, 13), date(2026, 7, 20))
    assert len(commits) == 1
    assert commits[0].sha == "abc"
    assert commits[0].message == "WT-1 fix"  # pierwsza linia, znormalizowana
    call = fake.calls[0]
    assert (call["owner"], call["repo"], call["author"]) == ("O", "R", "me@x.pl")
    assert call["since"].date() == date(2026, 7, 13)
    assert call["until"].date() == date(2026, 7, 20)  # następny poniedziałek (okno półotwarte)


def test_skips_commits_without_a_parseable_timestamp() -> None:
    source, _ = _source(
        [
            _raw("good", "WT-1", "2026-07-15T08:00:00Z"),
            _raw("bad", "WT-2", "niedata"),
        ]
    )
    commits = source.commits_for("me@x.pl", date(2026, 7, 13), date(2026, 7, 20))
    assert [c.sha for c in commits] == ["good"]


def test_empty_history_yields_no_commits() -> None:
    source, _ = _source([])
    assert source.commits_for("me@x.pl", date(2026, 7, 13), date(2026, 7, 20)) == []
