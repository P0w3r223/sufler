"""Testy konfiguracji drzwi GitHub (GithubSettings, ADR 0020) — from_env + walidacja.

Środowisko czyści globalny fixture z ``tests/conftest.py`` (zdejmuje wszystkie ``WORKMATE_*``).
Wcześniej stała tu ręczna lista siedemnastu zmiennych i pomocnik ``_clear`` — dwa testy
zapomniały go zawołać i ``test_enable_ci_auto_comment_defaults_false`` przewracał się na maszynie
z ``WORKMATE_GITHUB_ENABLE_CI_AUTO_COMMENT=true`` w powłoce. Lista, którą trzeba pamiętać
o uzupełnieniu przy każdej nowej zmiennej, jest gorszą izolacją niż brak listy.
"""

from __future__ import annotations

import pytest

from workmate.config import GithubSettings


def _valid(**kw) -> GithubSettings:
    base = {"token": "PAT", "owner": "biap", "repo": "workmate"}
    base.update(kw)
    return GithubSettings(**base)


def test_from_env_defaults():
    settings = GithubSettings.from_env()
    assert settings.token == ""
    assert settings.poll_interval_s == 60
    assert settings.per_page == 50
    assert settings.watch_kinds == ("issues", "comments")
    assert settings.enable_github_write is False


def test_from_env_reads_values(monkeypatch):
    monkeypatch.setenv("WORKMATE_GITHUB_TOKEN", "secret")
    monkeypatch.setenv("WORKMATE_GITHUB_OWNER", "biap")
    monkeypatch.setenv("WORKMATE_GITHUB_REPO", "workmate")
    monkeypatch.setenv("WORKMATE_GITHUB_POLL_INTERVAL", "45")
    monkeypatch.setenv("WORKMATE_GITHUB_ENABLE_WRITE", "true")
    settings = GithubSettings.from_env()
    assert settings.token == "secret"
    assert settings.owner == "biap"
    assert settings.poll_interval_s == 45
    assert settings.enable_github_write is True


def test_token_secret_not_in_repr():
    assert "secret" not in repr(_valid(token="secret"))


def test_validate_requires_token_owner_repo():
    with pytest.raises(ValueError, match="TOKEN|OWNER|REPO"):
        GithubSettings(token="", owner="", repo="").validate()


def test_validate_passes_with_required():
    _valid().validate()  # nie rzuca


def test_validate_rejects_poll_below_floor():
    with pytest.raises(ValueError, match="POLL_INTERVAL"):
        _valid(poll_interval_s=10).validate()


def test_validate_rejects_bad_per_page():
    with pytest.raises(ValueError, match="PER_PAGE"):
        _valid(per_page=0).validate()
    with pytest.raises(ValueError, match="PER_PAGE"):
        _valid(per_page=101).validate()


def test_validate_rejects_unknown_watch_kind():
    with pytest.raises(ValueError, match="WATCH_KINDS"):
        _valid(watch_kinds=("issues", "releases")).validate()


def test_validate_accepts_new_watch_kinds():
    # ADR 0024: pulls/reviews/ci wchodzą do dozwolonego zbioru obok issues/comments.
    _valid(watch_kinds=("issues", "comments", "pulls", "reviews", "ci")).validate()


def test_validate_rejects_reviews_without_issues_or_pulls():
    # Recenzje odkrywamy z otwartych PR w /issues — 'reviews' samo (lub tylko z 'ci') jest
    # funkcjonalnie martwe, więc walidacja odrzuca taką cichą pułapkę konfiguracji.
    with pytest.raises(ValueError, match="reviews"):
        _valid(watch_kinds=("reviews",)).validate()
    with pytest.raises(ValueError, match="reviews"):
        _valid(watch_kinds=("reviews", "ci")).validate()


def test_validate_accepts_reviews_with_pulls():
    _valid(watch_kinds=("pulls", "reviews")).validate()  # 'pulls' wystarcza jako źródło PR


def test_validate_rejects_empty_watch_kinds():
    with pytest.raises(ValueError, match="WATCH_KINDS"):
        _valid(watch_kinds=()).validate()


# --- ADR 0024 Faza 2: auto-komentarz CI ------------------------------------


def test_enable_ci_auto_comment_defaults_false():
    assert _valid().enable_ci_auto_comment is False
    assert GithubSettings.from_env().enable_ci_auto_comment is False


def test_from_env_reads_ci_auto_comment(monkeypatch):
    monkeypatch.setenv("WORKMATE_GITHUB_TOKEN", "secret")
    monkeypatch.setenv("WORKMATE_GITHUB_OWNER", "biap")
    monkeypatch.setenv("WORKMATE_GITHUB_REPO", "workmate")
    monkeypatch.setenv("WORKMATE_GITHUB_ENABLE_WRITE", "true")
    monkeypatch.setenv("WORKMATE_GITHUB_ENABLE_CI_AUTO_COMMENT", "true")
    assert GithubSettings.from_env().enable_ci_auto_comment is True


def test_validate_rejects_ci_auto_comment_without_write():
    # Auto-komentarz to ZAPIS — bez ogólnej bramki zapisu byłby martwy; odrzucamy cichą sprzeczność.
    with pytest.raises(ValueError, match="CI_AUTO_COMMENT"):
        _valid(enable_ci_auto_comment=True, enable_github_write=False).validate()


def test_validate_rejects_ci_auto_comment_without_ci_watch_kind():
    # Auto-komentarz CI potrzebuje zdarzeń CI — bez „ci" w WATCH_KINDS poller ich nie pobiera,
    # więc funkcja byłaby martwa mimo logu „WŁĄCZONY". Odrzucamy tę cichą sprzeczność.
    with pytest.raises(ValueError, match="WATCH_KINDS"):
        _valid(
            enable_ci_auto_comment=True,
            enable_github_write=True,
            watch_kinds=("issues", "comments"),
        ).validate()


def test_validate_accepts_ci_auto_comment_with_write_and_ci_watch_kind():
    _valid(
        enable_ci_auto_comment=True,
        enable_github_write=True,
        watch_kinds=("issues", "comments", "ci"),
    ).validate()  # nie rzuca


# --- strojenie estymacji czasu z commitów (ADR 0034 po cięciu) ---------------------
#
# Pokrętła przyjechały tu z ``JiraSettings``: po usunięciu zapisu worklogu dotyczą wyłącznie
# czytania commitów. Zdolność NIE MA bramki (odczyt jest domyślny), więc sufity muszą działać
# zawsze — nie ma flagi, za którą absurd mógłby przeczekać.


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("worklog_idle_gap_minutes", 1, "IDLE_GAP_MINUTES"),
        ("worklog_idle_gap_minutes", 5000, "IDLE_GAP_MINUTES"),
        ("worklog_ramp_up_minutes", -5, "RAMP_UP_MINUTES"),
        ("worklog_round_minutes", 7, "ROUND_MINUTES"),
        ("worklog_max_session_hours", 0, "MAX_SESSION_HOURS"),
        ("worklog_max_session_hours", 99.0, "MAX_SESSION_HOURS"),
        ("worklog_max_range_days", 0, "MAX_RANGE_DAYS"),
        ("worklog_max_range_days", 999, "MAX_RANGE_DAYS"),
    ],
)
def test_validate_rejects_out_of_range_worklog_numbers(field, value, match):
    with pytest.raises(ValueError, match=match):
        _valid(**{field: value}).validate()


def test_validate_rejects_ramp_up_longer_than_idle_gap():
    """Rozbieg dłuższy niż przerwa kończąca sesję dawałby estymacje nachodzące na siebie."""
    with pytest.raises(ValueError, match="nie może przekraczać"):
        _valid(worklog_idle_gap_minutes=30, worklog_ramp_up_minutes=60).validate()


def test_validate_rejects_unknown_timezone():
    """Literówka w nazwie strefy ma paść przy STARCIE, nie przy pierwszym wywołaniu narzędzia."""
    with pytest.raises(ValueError, match="WORKLOG_TZ"):
        _valid(worklog_tz="Europe/Warszawa").validate()


def test_validate_worklog_limits_needs_no_token_or_repo():
    """Drzwi Teams wołają SAM ten fragment — pełne ``validate`` wywróci wdrożenie bez GitHuba."""
    GithubSettings().validate_worklog_limits()  # nie rzuca


def test_from_env_reads_worklog_tuning(monkeypatch):
    monkeypatch.setenv("WORKMATE_GITHUB_WORKLOG_IDLE_GAP_MINUTES", "45")
    monkeypatch.setenv("WORKMATE_GITHUB_WORKLOG_ROUND_MINUTES", "30")
    monkeypatch.setenv("WORKMATE_GITHUB_WORKLOG_MAX_SESSION_HOURS", "6.5")
    monkeypatch.setenv("WORKMATE_GITHUB_WORKLOG_TZ", "  Europe/London  ")
    settings = GithubSettings.from_env()
    assert settings.worklog_idle_gap_minutes == 45
    assert settings.worklog_round_minutes == 30
    assert settings.worklog_max_session_hours == 6.5
    assert settings.worklog_tz == "Europe/London"  # przycięte


def test_from_env_worklog_defaults():
    settings = GithubSettings.from_env()
    assert settings.worklog_idle_gap_minutes == 90
    assert settings.worklog_ramp_up_minutes == 30
    assert settings.worklog_max_range_days == 31
    assert settings.worklog_tz == "Europe/Warsaw"
