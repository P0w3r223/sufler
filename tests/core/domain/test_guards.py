"""Testy strażników zapisu (``core/domain/guards.py``, ADR 0034/0035): izolacja osób,
sufit długości bez cichego cięcia, pełny kształt klucza Jiry (path-traversal, obcy projekt).
"""

from __future__ import annotations

import pytest

from sufler.core.domain.guards import (
    CrossPersonLeak,
    assert_single_person,
    bounded,
    require_jira_key,
)
from sufler.core.errors import WriteError

# --- assert_single_person ----------------------------------------------------------------


def test_assert_single_person_all_matching_is_silent():
    assert_single_person("alice", ["alice", "alice", "alice"])  # brak wyjątku = sukces


def test_assert_single_person_empty_entries_is_silent():
    assert_single_person("alice", [])  # brak wyjątku = sukces


def test_assert_single_person_foreign_entry_raises_cross_person_leak():
    with pytest.raises(CrossPersonLeak) as exc:
        assert_single_person("alice", ["alice", "bob"])
    assert "alice" in str(exc.value)
    assert "bob" in str(exc.value)


def test_assert_single_person_lists_all_foreign_ids_sorted():
    with pytest.raises(CrossPersonLeak) as exc:
        assert_single_person("alice", ["alice", "carol", "bob", "bob"])
    message = str(exc.value)
    assert message.index("bob") < message.index("carol")  # posortowane, bez duplikatów


# --- bounded -------------------------------------------------------------------------------


def test_bounded_under_limit_returns_text_unchanged():
    assert bounded("krotki tekst", 100, "opis") == "krotki tekst"


def test_bounded_exactly_at_limit_returns_text_unchanged():
    text = "x" * 50
    # Kryterium graniczne: dokładnie na limicie NIE tnie po cichu — zwraca cały tekst.
    assert bounded(text, 50, "opis") == text


def test_bounded_over_limit_raises_write_error_with_label_and_limit():
    with pytest.raises(WriteError) as exc:
        bounded("x" * 51, 50, "opis")
    message = str(exc.value)
    assert "opis" in message
    assert "50" in message


# --- require_jira_key ------------------------------------------------------------------


def test_require_jira_key_normalizes_case_and_whitespace():
    assert require_jira_key("  wm-12  ", "WM") == "WM-12"


@pytest.mark.parametrize(
    "issue_key",
    [
        "WM-1/../OPS-1",  # path-traversal — kluczowy przypadek z docstringu
        "WM-",
        "1WM-1",
        "WM-1x",
        "WM",
        "WM--1",
    ],
)
def test_require_jira_key_rejects_malformed_shapes(issue_key: str):
    with pytest.raises(WriteError):
        require_jira_key(issue_key, "WM")


def test_require_jira_key_rejects_foreign_project():
    with pytest.raises(WriteError) as exc:
        require_jira_key("OPS-1", "WM")
    assert "OPS-1" in str(exc.value)
    assert "WM" in str(exc.value)


def test_require_jira_key_accepts_matching_project():
    assert require_jira_key("WM-42", "WM") == "WM-42"
