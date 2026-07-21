"""Testy czystej domeny ewidencji czasu (ADR 0034) — grupowanie sesji i estymacja.

Wszystko liczone w pamięci na jawnych znacznikach czasu: brak I/O, brak zegara, brak
losowości. Znaczniki podajemy w UTC, a dobę kalendarzową liczymy w strefie z polityki.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from workmate.core.domain.worklog import (
    Commit,
    SessionPolicy,
    build_proposal,
    estimate_minutes,
    extract_issue_keys,
    group_sessions,
    local_day,
    normalize_commit_message,
    session_confidence,
)

_POLICY = SessionPolicy()
_SINCE, _UNTIL = date(2026, 7, 13), date(2026, 7, 19)


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    """Znacznik UTC w lipcu 2026 — skrót, żeby testy czytało się jak oś czasu."""
    return datetime(2026, 7, day, hour, minute, tzinfo=timezone.utc)


def _commit(sha: str, moment: datetime, message: str = "") -> Commit:
    return Commit(sha=sha, message=message, authored_at=moment)


# --- normalizacja wiadomości -----------------------------------------------------


def test_normalize_takes_first_line_only() -> None:
    assert normalize_commit_message("WT-1: naprawa\n\nDługi opis\nkolejna linia") == (
        "WT-1: naprawa"
    )


def test_normalize_strips_control_chars_without_raising() -> None:
    """Ścieżka ODCZYTU wycina znaki sterujące — dziwny commit nie może wywrócić raportu."""
    assert normalize_commit_message("WT-1:\x00 fix\x07 tu") == "WT-1: fix tu"


def test_normalize_truncates_with_ellipsis() -> None:
    result = normalize_commit_message("x" * 300, max_len=50)
    assert len(result) == 50
    assert result.endswith("…")


def test_normalize_handles_empty_message() -> None:
    assert normalize_commit_message("") == ""


# --- ekstrakcja kluczy Jira ------------------------------------------------------


def test_extract_keys_dedupes_and_preserves_order() -> None:
    message = "WT-12 i WT-3, znowu WT-12"
    assert extract_issue_keys(message) == ("WT-12", "WT-3")


def test_extract_keys_filters_by_project() -> None:
    assert extract_issue_keys("WT-1 oraz OPS-9", project="WT") == ("WT-1",)


def test_extract_keys_ignores_lowercase_and_embedded() -> None:
    """``wt-1`` to zwykły tekst, a ``WT-12abc`` nie jest kluczem — granice muszą trzymać."""
    assert extract_issue_keys("wt-1 WT-12abc") == ()


def test_extract_keys_does_not_confuse_longer_prefix() -> None:
    """``XWT-12`` to własny, poprawny klucz — a NIE ``WT-12`` wyłuskane ze środka."""
    assert extract_issue_keys("XWT-12") == ("XWT-12",)
    assert extract_issue_keys("XWT-12", project="WT") == ()


def test_extract_keys_returns_empty_for_message_without_keys() -> None:
    assert extract_issue_keys("porządki w README") == ()


# --- estymacja i pewność ---------------------------------------------------------


def test_estimate_single_commit_is_ramp_up_only() -> None:
    """Sesja jednocommitowa ma zerową rozpiętość — zostaje sam rozbieg."""
    assert estimate_minutes(0.0, _POLICY) == 30


def test_estimate_rounds_up_to_step() -> None:
    # 50 min rozpiętości + 30 min rozbiegu = 80 → w górę do 90 (krok 15).
    assert estimate_minutes(50.0, _POLICY) == 90


def test_estimate_clamps_to_max_session_hours() -> None:
    assert estimate_minutes(14 * 60, _POLICY) == int(_POLICY.max_session_hours * 60)


def test_confidence_low_for_single_commit() -> None:
    assert session_confidence(0.0, 1, _POLICY) == "low"


def test_confidence_low_when_clamped() -> None:
    """Sufit przyciął rozpiętość — wiemy, że wynik jest zły, więc nie udajemy pewności."""
    assert session_confidence(12 * 60, 9, _POLICY) == "low"


def test_confidence_high_for_dense_session() -> None:
    assert session_confidence(120.0, 4, _POLICY) == "high"


def test_confidence_medium_for_short_pair() -> None:
    assert session_confidence(10.0, 2, _POLICY) == "medium"


# --- doba kalendarzowa -----------------------------------------------------------


def test_local_day_uses_policy_offset() -> None:
    """23:30 UTC przy offsecie +120 min to już następna doba lokalna."""
    assert local_day(_at(15, 23, 30), 120) == date(2026, 7, 16)


def test_local_day_treats_naive_as_utc() -> None:
    assert local_day(datetime(2026, 7, 15, 10, 0), 0) == date(2026, 7, 15)


# --- grupowanie sesji ------------------------------------------------------------


def test_group_sessions_keeps_close_commits_together() -> None:
    commits = [_commit("a", _at(15, 9)), _commit("b", _at(15, 10)), _commit("c", _at(15, 10, 30))]
    sessions = group_sessions(commits, _POLICY)
    assert len(sessions) == 1
    assert sessions[0].commit_count == 3


def test_group_sessions_cuts_on_idle_gap() -> None:
    commits = [_commit("a", _at(15, 9)), _commit("b", _at(15, 14))]  # 5 h przerwy
    assert len(group_sessions(commits, _POLICY)) == 2


def test_group_sessions_cuts_on_day_boundary_even_within_gap() -> None:
    """Doba tnie TWARDO: wpis worklogu dotyczy jednego dnia, więc sesja nie przechodzi północy."""
    policy = SessionPolicy(tz_offset_minutes=0)
    commits = [_commit("a", _at(15, 23, 40)), _commit("b", _at(16, 0, 20))]  # 40 min przerwy
    sessions = group_sessions(commits, policy)
    assert len(sessions) == 2
    assert [session.day for session in sessions] == [date(2026, 7, 15), date(2026, 7, 16)]


def test_group_sessions_sorts_unordered_input() -> None:
    """API GitHuba oddaje commity malejąco — kolejność ustalamy sami."""
    commits = [_commit("b", _at(15, 10)), _commit("a", _at(15, 9))]
    sessions = group_sessions(commits, _POLICY)
    assert sessions[0].shas == ("a", "b")


def test_group_sessions_on_empty_input() -> None:
    assert group_sessions([], _POLICY) == ()


def test_session_collects_issue_keys_from_all_its_commits() -> None:
    commits = [
        _commit("a", _at(15, 9), "WT-1 start"),
        _commit("b", _at(15, 10), "WT-2 dalej"),
    ]
    assert group_sessions(commits, _POLICY)[0].issue_keys == ("WT-1", "WT-2")


# --- pełna propozycja ------------------------------------------------------------


def _proposal(commits: list[Commit], project: str = "WT"):
    return build_proposal(
        commits, since=_SINCE, until=_UNTIL, policy=_POLICY, project=project, author="piotr"
    )


def test_proposal_on_empty_commits_is_empty_not_a_crash() -> None:
    proposal = _proposal([])
    assert proposal.sessions == ()
    assert proposal.total_minutes == 0
    assert proposal.commit_count == 0


def test_proposal_warns_about_author_matching_when_no_commits() -> None:
    """Cichy zerowy wynik to najczęstsza pułapka (inny ``git config user.email``)."""
    assert any("author" in note for note in _proposal([]).notes)


def test_proposal_totals_match_sum_of_sessions_to_the_minute() -> None:
    commits = [
        _commit("a", _at(15, 9), "WT-1"),
        _commit("b", _at(15, 10), "WT-1"),
        _commit("c", _at(16, 9), "WT-2"),
    ]
    proposal = _proposal(commits)
    assert proposal.total_minutes == sum(session.minutes for session in proposal.sessions)
    assert proposal.total_minutes == sum(day.minutes for day in proposal.by_day)


def test_proposal_splits_shared_session_between_issues_exactly() -> None:
    """Podział w minutach z resztą do pierwszego klucza — sumy zgadzają się CO DO MINUTY."""
    commits = [
        _commit("a", _at(15, 9), "WT-1 start"),
        _commit("b", _at(15, 9, 45), "WT-2 dalej"),
    ]
    proposal = _proposal(commits)
    assert sum(issue.minutes for issue in proposal.by_issue) == proposal.total_minutes
    assert {issue.issue_key for issue in proposal.by_issue} == {"WT-1", "WT-2"}


def test_proposal_moves_keyless_session_to_unattributed() -> None:
    commits = [_commit("a", _at(15, 9), "porządki w README")]
    proposal = _proposal(commits)
    assert proposal.by_issue == ()
    assert proposal.unattributed_minutes == proposal.total_minutes
    assert any("unattributed_hours" in note for note in proposal.notes)


def test_proposal_ignores_keys_from_other_projects() -> None:
    """``OPS-9`` wspomniane mimochodem nie może wejść do zestawienia projektu ``WT``."""
    commits = [_commit("a", _at(15, 9), "OPS-9 wzmianka")]
    proposal = _proposal(commits, project="WT")
    assert proposal.by_issue == ()
    assert proposal.unattributed_minutes == proposal.total_minutes


def test_proposal_orders_issues_by_time_descending() -> None:
    commits = [
        _commit("a", _at(15, 9), "WT-2"),
        _commit("b", _at(16, 9), "WT-1"),
        _commit("c", _at(16, 10), "WT-1"),
        _commit("d", _at(16, 11), "WT-1"),
    ]
    proposal = _proposal(commits)
    assert proposal.by_issue[0].issue_key == "WT-1"


def test_proposal_hours_are_derived_from_minutes() -> None:
    commits = [_commit("a", _at(15, 9), "WT-1"), _commit("b", _at(15, 10), "WT-1")]
    proposal = _proposal(commits)
    assert proposal.total_hours == round(proposal.total_minutes / 60, 2)


def test_proposal_carries_disclaimer() -> None:
    """Propozycja ZAWSZE niesie zastrzeżenie — model ma je przekazać użytkownikowi."""
    assert "ESTYMACJA" in _proposal([]).disclaimer


def test_proposal_day_bucket_uses_policy_timezone() -> None:
    """Commit o 23:30 UTC ląduje w następnej dobie przy offsecie +120 min."""
    proposal = build_proposal(
        [_commit("a", _at(15, 23, 30), "WT-1")],
        since=_SINCE,
        until=_UNTIL,
        policy=SessionPolicy(tz_offset_minutes=120),
        project="WT",
    )
    assert proposal.by_day[0].day == date(2026, 7, 16)


def test_proposal_long_range_produces_one_session_per_day() -> None:
    commits = [_commit(f"c{i}", _at(13, 9) + timedelta(days=i), "WT-1") for i in range(5)]
    proposal = _proposal(commits)
    assert len(proposal.by_day) == 5
