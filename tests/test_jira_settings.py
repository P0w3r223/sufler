"""Testy ``JiraSettings`` (ADR 0030/0033, zawężone do odczytu przez ADR 0054).

Poller/zapis/tranzycja zniknęły razem z mostem — zostaje kontrakt czysto odczytowy: URL + token,
wariant wdrożenia (server/cloud), e-mail wymagany na Cloud, i ``my_account`` dla "moich zadań"
na serwerze MCP.
"""

from __future__ import annotations

import pytest

from sufler.config import JiraSettings


def test_validate_ok_does_not_raise():
    JiraSettings(base_url="https://jira.example", token="secret").validate()


def test_validate_requires_url_and_token():
    with pytest.raises(ValueError, match="URL i tokenu"):
        JiraSettings().validate()


def test_from_env_reads_and_normalizes(monkeypatch):
    monkeypatch.setenv("SUFLER_JIRA_BASE_URL", "https://jira.example/")
    monkeypatch.setenv("SUFLER_JIRA_TOKEN", "secret")
    monkeypatch.setenv("SUFLER_JIRA_MY_ACCOUNT", " mikolaj@example.org ")

    settings = JiraSettings.from_env()
    assert settings.base_url == "https://jira.example"  # trailing slash ucięty
    assert settings.token == "secret"
    assert settings.my_account == "mikolaj@example.org"  # otoczenie białych znaków ucięte


def test_validate_default_deployment_is_server_no_email_required():
    JiraSettings(base_url="https://jira.example", token="secret").validate()  # nie rzuca


def test_validate_cloud_requires_email():
    with pytest.raises(ValueError, match="SUFLER_JIRA_EMAIL"):
        JiraSettings(
            base_url="https://acme.atlassian.net", token="secret", deployment="cloud"
        ).validate()


def test_validate_cloud_ok_with_email():
    JiraSettings(
        base_url="https://acme.atlassian.net",
        token="secret",
        deployment="cloud",
        email="me@example.com",
    ).validate()  # nie rzuca


def test_validate_rejects_unknown_deployment():
    settings = JiraSettings(base_url="https://jira.example", token="secret", deployment="onprem")
    with pytest.raises(ValueError, match="SUFLER_JIRA_DEPLOYMENT"):
        settings.validate()


def test_from_env_reads_and_normalizes_deployment_and_email(monkeypatch):
    monkeypatch.setenv("SUFLER_JIRA_DEPLOYMENT", "CLOUD")
    monkeypatch.setenv("SUFLER_JIRA_EMAIL", "  me@example.com  ")

    settings = JiraSettings.from_env()
    assert settings.deployment == "cloud"  # lowercased
    assert settings.email == "me@example.com"  # otoczenie białych znaków ucięte


def test_from_env_default_deployment_when_unset(monkeypatch):
    monkeypatch.delenv("SUFLER_JIRA_DEPLOYMENT", raising=False)
    assert JiraSettings.from_env().deployment == "server"


def test_from_env_default_my_account_is_empty(monkeypatch):
    monkeypatch.delenv("SUFLER_JIRA_MY_ACCOUNT", raising=False)
    assert JiraSettings.from_env().my_account == ""
