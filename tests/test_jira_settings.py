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


# --- bramka zapisu (Gate 5 / ADR 0031) --------------------------------------


def test_validate_ok_read_only_does_not_require_write_fields():
    _settings().validate()  # enable_jira_write domyślnie False → brak wymogu projektu/konta


def test_validate_write_requires_write_project():
    with pytest.raises(ValueError, match="WRITE_PROJECT"):
        _settings(enable_jira_write=True, self_account="svc").validate()


def test_validate_write_requires_self_account():
    with pytest.raises(ValueError, match="SELF_ACCOUNT"):
        _settings(enable_jira_write=True, write_project="WM").validate()


def test_validate_write_ok_with_project_and_account():
    _settings(enable_jira_write=True, write_project="WM", self_account="svc").validate()


def test_from_env_reads_write_fields(monkeypatch):
    monkeypatch.setenv("WORKMATE_JIRA_BASE_URL", "https://jira.x")
    monkeypatch.setenv("WORKMATE_JIRA_TOKEN", "secret")
    monkeypatch.setenv("WORKMATE_JIRA_WATCH_PROJECTS", "WM")
    monkeypatch.setenv("WORKMATE_JIRA_ENABLE_WRITE", "true")
    monkeypatch.setenv("WORKMATE_JIRA_WRITE_PROJECT", "wm")
    monkeypatch.setenv("WORKMATE_JIRA_DEFAULT_ISSUE_TYPE", "Bug")
    settings = JiraSettings.from_env()
    assert settings.enable_jira_write is True
    assert settings.write_project == "WM"  # znormalizowane do wielkich liter
    assert settings.default_issue_type == "Bug"


# --- bramka tranzycji (ADR 0032) --------------------------------------------


def test_validate_transition_requires_write_project():
    with pytest.raises(ValueError, match="WRITE_PROJECT"):
        _settings(enable_jira_transition=True, self_account="svc").validate()


def test_validate_transition_requires_self_account():
    with pytest.raises(ValueError, match="SELF_ACCOUNT"):
        _settings(enable_jira_transition=True, write_project="WM").validate()


def test_validate_transition_only_profile_ok_without_write():
    # Profil „tylko-tranzycja": enable_jira_write pozostaje False, bramka tranzycji wystarcza.
    _settings(enable_jira_transition=True, write_project="WM", self_account="svc").validate()


def test_validate_bounds_max_transition_hops():
    with pytest.raises(ValueError, match="MAX_TRANSITION_HOPS"):
        _settings(max_transition_hops=0).validate()
    with pytest.raises(ValueError, match="MAX_TRANSITION_HOPS"):
        _settings(max_transition_hops=11).validate()  # sufit = 10


def test_validate_max_hops_bounds_apply_even_when_gate_off():
    # Zakres hopów walidujemy bezwarunkowo — absurd to twardy błąd niezależnie od bramki.
    with pytest.raises(ValueError, match="MAX_TRANSITION_HOPS"):
        _settings(enable_jira_transition=False, max_transition_hops=0).validate()


def test_validate_default_single_hop_is_in_range():
    _settings().validate()  # domyślnie max_transition_hops=1 (single-hop, pilotaż)


def test_from_env_reads_transition_fields(monkeypatch):
    monkeypatch.setenv("WORKMATE_JIRA_BASE_URL", "https://jira.x")
    monkeypatch.setenv("WORKMATE_JIRA_TOKEN", "secret")
    monkeypatch.setenv("WORKMATE_JIRA_WATCH_PROJECTS", "WM")
    monkeypatch.setenv("WORKMATE_JIRA_ENABLE_TRANSITION", "true")
    monkeypatch.setenv("WORKMATE_JIRA_MAX_TRANSITION_HOPS", "3")
    settings = JiraSettings.from_env()
    assert settings.enable_jira_transition is True
    assert settings.max_transition_hops == 3


# --- wariant wdrożenia: Server/DC vs Cloud (ADR 0033) -----------------------


def test_validate_default_deployment_is_server_no_email_required():
    # Domyślny deployment="server" nie wymaga e-maila (PAT Bearer) — wstecznie zgodne.
    settings = _settings()
    assert settings.deployment == "server"
    settings.validate()


def test_validate_cloud_requires_email():
    with pytest.raises(ValueError, match="EMAIL"):
        _settings(deployment="cloud").validate()


def test_validate_cloud_ok_with_email():
    _settings(deployment="cloud", email="me@example.com").validate()


def test_validate_rejects_unknown_deployment():
    with pytest.raises(ValueError, match="DEPLOYMENT"):
        _settings(deployment="datacenter").validate()


def test_from_env_reads_and_normalizes_deployment_and_email(monkeypatch):
    monkeypatch.setenv("WORKMATE_JIRA_BASE_URL", "https://acme.atlassian.net")
    monkeypatch.setenv("WORKMATE_JIRA_TOKEN", "api-token")
    monkeypatch.setenv("WORKMATE_JIRA_WATCH_PROJECTS", "WM")
    monkeypatch.setenv("WORKMATE_JIRA_DEPLOYMENT", "Cloud")  # dowolna wielkość liter
    monkeypatch.setenv("WORKMATE_JIRA_EMAIL", "  me@example.com  ")
    settings = JiraSettings.from_env()
    assert settings.deployment == "cloud"  # znormalizowane do małych liter
    assert settings.email == "me@example.com"  # przycięte
    settings.validate()


def test_from_env_default_deployment_when_unset(monkeypatch):
    monkeypatch.delenv("WORKMATE_JIRA_DEPLOYMENT", raising=False)
    monkeypatch.setenv("WORKMATE_JIRA_BASE_URL", "https://jira.x")
    monkeypatch.setenv("WORKMATE_JIRA_TOKEN", "secret")
    monkeypatch.setenv("WORKMATE_JIRA_WATCH_PROJECTS", "WM")
    assert JiraSettings.from_env().deployment == "server"


# --- tożsamość konta a wariant wdrożenia ------------------------------------------


def test_cloud_rejects_a_server_login_as_self_account():
    """Login Server/DC po przełączeniu na Cloud = CICHO martwy strażnik pętli self-skip.

    Na Cloud aktor zdarzenia to ``accountId``, więc porównanie z loginem nigdy nie trafia:
    poller i drzwi zapisu zaczynają odsyłać sobie nawzajem własne zapisy. Wcześniej
    walidacja patrzyła tylko na to, czy wartość jest NIEPUSTA.
    """
    with pytest.raises(ValueError, match="wygląda na login Server/DC"):
        _settings(deployment="cloud", email="a@b.pl", self_account="psmit").validate()


@pytest.mark.parametrize(
    "account",
    ["712020:c0ffee00-0000-4000-8000-000000000008", "5b10ac8d82e05b22cc7d4ef5"],
)
def test_cloud_accepts_both_account_id_shapes(account: str):
    _settings(deployment="cloud", email="a@b.pl", self_account=account).validate()


def test_server_still_accepts_a_plain_login():
    """Ścieżka Server/DC nietknięta — tam login PAT jest poprawną tożsamością."""
    _settings(self_account="psmit").validate()
