"""Testy czystej logiki celu wątku (resolve_thread_target, ADR 0024) — url zdarzenia → cel."""

from __future__ import annotations

import pytest

from sufler.core.domain.threads import resolve_thread_target


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
    assert resolve_thread_target("https://github.com/o/r/pull/12/issues/7") == ("pr", "12")


def test_unrelated_url_has_no_target():
    assert resolve_thread_target("https://github.com/o/r/commit/abc123") is None


# --- ADR 0024 B2: wątkowanie kanału dla Jiry (url /browse/{KEY}) -----------------------


def test_browse_url_resolves_to_jira_target():
    # Kształt url, który emituje jira/selection._browse_url przy utworzeniu/tranzycji.
    assert resolve_thread_target("https://example.atlassian.net/browse/WM-5") == ("jira", "WM-5")


def test_browse_comment_url_shares_same_issue_thread():
    """Komentarz niesie ``?focusedCommentId=`` — cel wątku to nadal KLUCZ, nie sufiks.

    Dzięki temu utworzenie/tranzycja/komentarz jednego zgłoszenia (te same trzy kształty url
    z ``jira/selection._browse_url``) trafiają do TEGO SAMEGO wątku na kanale.
    """
    url = "https://example.atlassian.net/browse/WM-5?focusedCommentId=123"
    assert resolve_thread_target(url) == ("jira", "WM-5")


def test_browse_lowercase_key_normalised_to_upper():
    # Klucze Jira są uppercase — ``wm-5`` i ``WM-5`` muszą trafić do jednego wątku.
    assert resolve_thread_target("https://example.atlassian.net/browse/wm-5") == ("jira", "WM-5")


@pytest.mark.parametrize(
    "key,expected",
    [
        ("WM-5", "WM-5"),
        ("OPS-123", "OPS-123"),
        ("ABC1-45", "ABC1-45"),
        ("Ops-7", "OPS-7"),  # mieszana wielkość → uppercase
    ],
)
def test_browse_multichar_keys_with_digits(key, expected):
    assert resolve_thread_target(f"https://j/browse/{key}") == ("jira", expected)


def test_pull_takes_priority_over_jira_fragment():
    """PR sprawdzamy PIERWSZE — url z oboma wzorcami rozstrzyga na korzyść ``/pull/``, nie Jiry."""
    url = "https://github.com/o/r/pull/12/browse/WM-5"
    assert resolve_thread_target(url) == ("pr", "12")


def test_issue_takes_priority_over_jira_fragment():
    """``/issues/`` przed Jirą — GitHub nigdy nie rozwiązuje się na cel ``jira``."""
    url = "https://github.com/o/r/issues/7/browse/WM-5"
    assert resolve_thread_target(url) == ("issue", "7")


@pytest.mark.parametrize(
    "url",
    [
        "https://j/browse/",  # brak klucza
        "https://j/browse/WM",  # klucz bez numeru
        "https://j/browse/W-5",  # jednoliterowy projekt — poniżej _JIRA_KEY_RE (min 2 znaki)
    ],
)
def test_malformed_browse_url_has_no_target(url):
    # Zła forma klucza nie zawiąże fałszywego wątku — spójne z _JIRA_KEY_RE (application/jira).
    assert resolve_thread_target(url) is None
