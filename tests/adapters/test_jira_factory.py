"""Testy fabryki build_jira_client (ADR 0033) — wybór implementacji wg deployment."""

from __future__ import annotations

import httpx

from workmate.adapters.outbound.jira_api import HttpxJiraClient, build_jira_client
from workmate.adapters.outbound.jira_cloud_api import HttpxJiraCloudClient
from workmate.config import JiraSettings


def _settings(**kw) -> JiraSettings:
    base: dict = {
        "base_url": "https://acme.example",
        "token": "secret",
    }
    base.update(kw)
    return JiraSettings(**base)


def test_factory_default_builds_server_client():
    client = build_jira_client(httpx.Client(), _settings())
    assert isinstance(client, HttpxJiraClient)


def test_factory_server_deployment_builds_server_client():
    client = build_jira_client(httpx.Client(), _settings(deployment="server"))
    assert isinstance(client, HttpxJiraClient)


def test_factory_cloud_deployment_builds_cloud_client():
    client = build_jira_client(
        httpx.Client(), _settings(deployment="cloud", email="me@example.com")
    )
    assert isinstance(client, HttpxJiraCloudClient)


def test_factory_cloud_is_case_insensitive():
    client = build_jira_client(
        httpx.Client(), _settings(deployment="Cloud", email="me@example.com")
    )
    assert isinstance(client, HttpxJiraCloudClient)
