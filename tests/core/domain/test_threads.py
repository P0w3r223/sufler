"""Testy czystej logiki celu wątku (resolve_thread_target, ADR 0024) — url zdarzenia → cel."""
from __future__ import annotations

import pytest

from workmate.core.domain.threads import resolve_thread_target


def test_pull_url_resolves_to_pr_target():
    assert resolve_thread_target("https://github.com/o/r/pull/12") == ("pr", "12")


def test_issue_url_resolves_to_issue_target():
    assert resolve_thread_target("https://github.com/o/r/issues/7") == ("issue", "7")


def test_ci_run_url_without_pull_or_issue_has_no_target():
    # Przebieg CI niezwiązany z PR (url wskazuje run, nie stronę PR) → brak celu wątku.
    assert resolve_thread_target("https://github.com/o/r/actions/runs/9") is None


@pytest.mark.parametrize("url", ["", None])
def test_empty_or_none_url_has_no_target(url):
    assert resolve_thread_target(url) is None


def test_pull_takes_priority_over_issue_fragment():
    """PR sprawdzamy PIERWSZE — gdy url niesie oba wzorce, wygrywa ``/pull/``, nie ``/issues/``.

    Priorytet PR chroni przed rozjazdem: komentarz na PR (którego url zawiera ``/pull/{n}``)
    nie może wpaść do osobnego wątku „issue" o tym samym numerze. Url z oboma segmentami
    jednoznacznie odsłania kolejność dopasowania.
    """
    assert resolve_thread_target(
        "https://github.com/o/r/pull/12/issues/7"
    ) == ("pr", "12")


def test_unrelated_url_has_no_target():
    assert resolve_thread_target("https://github.com/o/r/commit/abc123") is None
