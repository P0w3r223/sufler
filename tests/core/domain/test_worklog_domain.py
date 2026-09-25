"""Testy czystej domeny ewidencji czasu (ADR 0034) — grupowanie sesji i estymacja.

Wszystko liczone w pamięci na jawnych znacznikach czasu: brak I/O, brak zegara, brak
losowości. Znaczniki podajemy w UTC, a dobę kalendarzową liczymy w strefie z polityki.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sufler.core.domain.worklog import (
    Commit,
    SessionPolicy,
    build_proposal,
    estimate_minutes,
    extract_issue_keys,
    group_sessions,
    local_day,
    map_github_commits,
    normalize_commit_message,
    session_confidence,
)

_POLICY = SessionPolicy()
_UTC = ZoneInfo("UTC")
_WARSAW = ZoneInfo("Europe/Warsaw")
_SINCE, _UNTIL = date(2026, 7, 13), date(2026, 7, 19)


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    """Znacznik UTC w lipcu 2026 — skrót, żeby testy czytało się jak oś czasu."""
    return datetime(2026, 7, day, hour, minute, tzinfo=UTC)


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


def test_extract_keys_takes_every_project_prefix() -> None:
    """Bez zawężania do projektu: to raport, a praca nad ``OPS-9`` też się odbyła."""
    assert extract_issue_keys("WT-1 oraz OPS-9") == ("WT-1", "OPS-9")


def test_extract_keys_ignores_lowercase_and_embedded() -> None:
    """``wt-1`` to zwykły tekst, a ``WT-12abc`` nie jest kluczem — granice muszą trzymać."""
    assert extract_issue_keys("wt-1 WT-12abc") == ()


def test_extract_keys_does_not_confuse_longer_prefix() -> None:
    """``XWT-12`` to własny, poprawny klucz — a NIE ``WT-12`` wyłuskane ze środka."""
    assert extract_issue_keys("XWT-12") == ("XWT-12",)


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


def test_local_day_uses_policy_timezone() -> None:
    """23:30 UTC to w Warszawie (letni czas, +2 h) już następna doba."""
    assert local_day(_at(15, 23, 30), ZoneInfo("Europe/Warsaw")) == date(2026, 7, 16)


def test_local_day_follows_dst_instead_of_a_fixed_offset() -> None:
    """Zimą Warszawa ma +1 h, więc ta sama godzina UTC zostaje w TEJ SAMEJ dobie.

    Regresja wobec stałego offsetu +120 min (pierwotny ADR 0034): tamten przez pół roku
    przesuwał granicę doby o godzinę, więc commity z okolic północy lądowały w złym dniu.
    """
    winter = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)
    assert local_day(winter, ZoneInfo("Europe/Warsaw")) == date(2026, 1, 16)
    assert local_day(datetime(2026, 1, 15, 22, 30, tzinfo=UTC), _WARSAW) == date(2026, 1, 15)


def test_local_day_treats_naive_as_utc() -> None:
    assert local_day(datetime(2026, 7, 15, 10, 0), _UTC) == date(2026, 7, 15)


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
    policy = SessionPolicy(tz=_UTC)
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


def _proposal(commits: list[Commit]):
    return build_proposal(commits, since=_SINCE, until=_UNTIL, policy=_POLICY, author="piotr")


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


def test_proposal_reports_keys_of_every_project() -> None:
    """Zawężanie do projektu odeszło razem z zapisem — dziś nie ma DOKĄD kierować zestawienia."""
    commits = [_commit("a", _at(15, 9), "OPS-9 wzmianka")]
    proposal = _proposal(commits)
    assert [total.issue_key for total in proposal.by_issue] == ["OPS-9"]
    assert proposal.unattributed_minutes == 0


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
    """Commit o 23:30 UTC ląduje w następnej dobie warszawskiej (letni czas)."""
    proposal = build_proposal(
        [_commit("a", _at(15, 23, 30), "WT-1")],
        since=_SINCE,
        until=_UNTIL,
        policy=SessionPolicy(tz=_WARSAW),
    )
    assert proposal.by_day[0].day == date(2026, 7, 16)


def test_proposal_long_range_produces_one_session_per_day() -> None:
    commits = [_commit(f"c{i}", _at(13, 9) + timedelta(days=i), "WT-1") for i in range(5)]
    proposal = _proposal(commits)
    assert len(proposal.by_day) == 5


# --- objętość odpowiedzi i ostrzeżenie o ucięciu ----------------------------------


def test_session_shas_are_a_sample_not_the_full_list() -> None:
    """Pełna lista SHA szła w całości do kontekstu modelu — przy 500 commitach kilkanaście KB.

    Licznik zostaje pełny (``commit_count``), więc nic nie ginie poza objętością.
    """
    commits = [_commit(f"sha{index}", _at(15, 9) + timedelta(minutes=index)) for index in range(20)]
    session = group_sessions(commits, _POLICY)[0]
    assert session.commit_count == 20
    assert len(session.shas) == 5
    assert session.shas[0] == "sha0"


def test_truncated_history_is_announced_in_notes() -> None:
    """Ucięcie zaniża godziny — bez noty propozycja wygląda na kompletną."""
    proposal = build_proposal(
        [_commit("a", _at(15, 9), "WT-1")],
        since=_SINCE,
        until=_UNTIL,
        policy=_POLICY,
        truncated=True,
    )
    assert any("UCIĘTA" in note for note in proposal.notes)


def test_complete_history_carries_no_truncation_note() -> None:
    assert not any("UCIĘTA" in note for note in _proposal([]).notes)


# --- mapowanie surowego JSON GitHuba (biała lista, ADR 0034/0036) -----------------
#
# ``map_github_commits`` to JEDYNY, współdzielony mapper propozycji czasu (ADR 0034) i źródła
# commitów kart czasu (ADR 0036) — jego biała lista pól i odporność na dziwny kształt muszą być
# testowane WPROST, nie tylko przez ścieżki, które go wołają.


def _raw(
    sha: str = "abc",
    message: str = "WT-7 robota",
    when: str = "2026-07-15T09:12:00Z",
    login: str = "P0w3r223",
    email: str = "piotr@example.com",
) -> dict:
    return {
        "sha": sha,
        "html_url": f"https://github.com/x/y/commit/{sha}",
        "author": {"login": login},
        "commit": {"message": message, "author": {"date": when, "email": email}},
    }


def test_map_whitelists_every_field_and_normalizes_the_message() -> None:
    (commit,) = map_github_commits([_raw(message="WT-7 robota\n\ndługi opis")])
    assert commit.sha == "abc"
    assert commit.message == "WT-7 robota"  # pierwsza linia, znormalizowana
    assert commit.authored_at == datetime(2026, 7, 15, 9, 12, tzinfo=UTC)
    assert commit.author_login == "P0w3r223"  # z item.author.login (konto GitHub)
    assert commit.author_email == "piotr@example.com"  # z item.commit.author.email (autor git)
    assert commit.url == "https://github.com/x/y/commit/abc"


def test_map_skips_entries_without_a_parseable_timestamp() -> None:
    """Ścieżka ODCZYTU: daty nie zgadujemy — dziwny commit odpada, nie wywraca raportu."""
    raw = [_raw("good"), _raw("bad", when="kiedyś"), _raw("empty", when="")]
    assert [c.sha for c in map_github_commits(raw)] == ["good"]


def test_map_skips_non_dict_items_in_the_list() -> None:
    """Element listy o nie-obiektowym kształcie (śmieć z API) pomijamy zamiast rzucać."""
    assert [c.sha for c in map_github_commits([_raw("ok"), "śmieć", 123, None])] == ["ok"]  # type: ignore[list-item]


def test_map_tolerates_missing_nested_objects_with_safe_defaults() -> None:
    """``_as_dict`` osłania brak/dziwny kształt ``commit``/``author`` — pola opcjonalne → puste.

    Znacznik czasu wciąż jest (żyje w ``commit.author.date``), więc commit MAPUJE się z pustym
    loginem, e-mailem i URL-em zamiast wysypać cały przebieg.
    """
    raw = {"sha": "s", "commit": {"author": {"date": "2026-07-15T09:12:00Z"}}}
    (commit,) = map_github_commits([raw])
    assert commit.sha == "s"
    empty = (commit.author_login, commit.author_email, commit.url, commit.message)
    assert empty == ("", "", "", "")


def test_map_ignores_a_non_dict_commit_object() -> None:
    """Gdy ``commit`` nie jest słownikiem, brak znacznika czasu → wpis odpada (nie rzuca)."""
    assert map_github_commits([{"sha": "s", "commit": "nie-slownik"}]) == []


def test_map_on_empty_input_yields_no_commits() -> None:
    assert map_github_commits([]) == []
