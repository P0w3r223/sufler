"""Testy JiraSettings (ADR 0030) — from_env + validate (fail-fast)."""

from __future__ import annotations

import pytest

from workmate.config import JiraSettings


def _settings(**kw) -> JiraSettings:
    base: dict = {"base_url": "https://jira.example.com", "token": "PAT", "watch_projects": ("WM",)}
    base.update(kw)
    return JiraSettings(**base)


def test_validate_ok_does_not_raise():
    _settings().validate()


def test_validate_requires_url_and_token():
    with pytest.raises(ValueError, match="URL i tokenu"):
        _settings(base_url="").validate()
    with pytest.raises(ValueError, match="URL i tokenu"):
        _settings(token="").validate()


def test_validate_requires_watch_projects():
    with pytest.raises(ValueError, match="WATCH_PROJECTS"):
        _settings(watch_projects=()).validate()


def test_validate_poll_floor_and_per_page_range():
    with pytest.raises(ValueError, match="POLL_INTERVAL"):
        _settings(poll_interval_s=5).validate()
    with pytest.raises(ValueError, match="PER_PAGE"):
        _settings(per_page=0).validate()


def test_from_env_reads_and_normalizes(monkeypatch):
    monkeypatch.setenv("WORKMATE_JIRA_BASE_URL", "https://jira.x/")
    monkeypatch.setenv("WORKMATE_JIRA_TOKEN", "secret")
    monkeypatch.setenv("WORKMATE_JIRA_WATCH_PROJECTS", "WM, OPS")
    settings = JiraSettings.from_env()
    assert settings.base_url == "https://jira.x"  # rstrip '/'
    assert settings.token == "secret"
    assert settings.watch_projects == ("WM", "OPS")
