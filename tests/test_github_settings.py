"""Testy konfiguracji drzwi GitHub (GithubSettings, ADR 0020) — from_env + walidacja."""

from __future__ import annotations

import pytest

from workmate.config import GithubSettings

_GITHUB_VARS = (
    "WORKMATE_GITHUB_TOKEN",
    "WORKMATE_GITHUB_OWNER",
    "WORKMATE_GITHUB_REPO",
    "WORKMATE_GITHUB_API_BASE",
    "WORKMATE_GITHUB_POLL_INTERVAL",
    "WORKMATE_GITHUB_PER_PAGE",
    "WORKMATE_GITHUB_WATCH_KINDS",
    "WORKMATE_GITHUB_ENABLE_WRITE",
    "WORKMATE_GITHUB_ENABLE_CI_AUTO_COMMENT",
    "WORKMATE_GITHUB_STATE",
    "WORKMATE_GITHUB_SELF_LOGIN",
)


def _clear(monkeypatch):
    for var in _GITHUB_VARS:
        monkeypatch.delenv(var, raising=False)


def _valid(**kw) -> GithubSettings:
    base = {"token": "PAT", "owner": "biap", "repo": "workmate"}
    base.update(kw)
    return GithubSettings(**base)


def test_from_env_defaults(monkeypatch):
    _clear(monkeypatch)
    settings = GithubSettings.from_env()
    assert settings.token == ""
    assert settings.poll_interval_s == 60
    assert settings.per_page == 50
    assert settings.watch_kinds == ("issues", "comments")
    assert settings.enable_github_write is False


def test_from_env_reads_values(monkeypatch):
    _clear(monkeypatch)
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
    _clear(monkeypatch)
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
