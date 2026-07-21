"""Testy wiringu ``_build_worklog_catalog`` (ADR 0034) — trzecia, niezależna bramka Jiry.

Sedno: ewidencja czasu stoi NA DWÓCH nogach (Jira = zapis wpisu, GitHub = źródło commitów), więc
włączona bramka bez którejkolwiek z nich to twardy błąd startu, a nie narzędzie, które okaże się
puste przy pierwszym użyciu. Bramka OFF → pusty katalog, czyli model nie widzi ani zapisu, ani
odczytu commitów. Golden-test powierzchni MCP zostaje nietknięty — wchodzimy przez
``extra_catalog``, nie przez ``build_tool_catalog``.
"""

from __future__ import annotations

import pytest

from workmate.adapters.inbound.teams_graph.app import _build_worklog_catalog
from workmate.config import GithubSettings, JiraSettings

# Budowa katalogu nie dotyka magazynu zdarzeń (wstrzykuje go tylko do serwisu) — None wystarcza.
_EVENTS = None


def _jira(**kw) -> JiraSettings:
    base: dict = {
        "base_url": "https://example.atlassian.net",
        "token": "api-token",
        "deployment": "cloud",
        "email": "piotr@example.com",
        "watch_projects": ("WT",),
        "write_project": "WT",
        "self_account": "712020:4788230b",
    }
    base.update(kw)
    return JiraSettings(**base)


def _github(**kw) -> GithubSettings:
    base: dict = {
        "token": "gh-pat",
        "owner": "BIAP-Inteligentne-Technologie",
        "repo": "PIWorkmate",
    }
    base.update(kw)
    return GithubSettings(**base)


def _names(jira: JiraSettings, github: GithubSettings | None = None) -> set[str]:
    return {spec.name for spec in _build_worklog_catalog(jira, github or _github(), _EVENTS)}


def test_gate_off_yields_empty_catalog() -> None:
    """Bez bramki model nie dostaje nawet odczytu commitów — profil per drzwi."""
    assert _build_worklog_catalog(_jira(), _github(), _EVENTS) == []


def test_gate_on_yields_both_worklog_tools() -> None:
    assert _names(_jira(enable_jira_worklog=True)) == {"propose_worklog", "log_jira_worklog"}


def test_write_gate_alone_does_not_enable_worklog() -> None:
    """Bramki są NIEZALEŻNE: zapis zgłoszeń nie otwiera ewidencji czasu."""
    assert _build_worklog_catalog(_jira(enable_jira_write=True), _github(), _EVENTS) == []


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("token", "WORKMATE_JIRA_TOKEN"),
        ("base_url", "WORKMATE_JIRA_BASE_URL"),
        ("write_project", "WORKMATE_JIRA_WRITE_PROJECT"),
        ("self_account", "WORKMATE_JIRA_SELF_ACCOUNT"),
    ],
)
def test_missing_jira_target_is_a_hard_start_error(field: str, expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        _build_worklog_catalog(_jira(enable_jira_worklog=True, **{field: ""}), _github(), _EVENTS)


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("token", "WORKMATE_GITHUB_TOKEN"),
        ("owner", "WORKMATE_GITHUB_OWNER"),
        ("repo", "WORKMATE_GITHUB_REPO"),
    ],
)
def test_missing_commit_source_is_a_hard_start_error(field: str, expected: str) -> None:
    """Bez commitów propozycja jest martwa — to POŁOWA zdolności, nie opcjonalny dodatek."""
    with pytest.raises(ValueError, match=expected):
        _build_worklog_catalog(_jira(enable_jira_worklog=True), _github(**{field: ""}), _EVENTS)


def test_cloud_without_email_is_a_hard_start_error() -> None:
    with pytest.raises(ValueError, match="WORKMATE_JIRA_EMAIL"):
        _build_worklog_catalog(_jira(enable_jira_worklog=True, email=""), _github(), _EVENTS)


def test_server_deployment_does_not_require_email() -> None:
    settings = _jira(enable_jira_worklog=True, deployment="server", email="")
    assert _names(settings) == {"propose_worklog", "log_jira_worklog"}


def test_unknown_deployment_is_rejected() -> None:
    with pytest.raises(ValueError, match="DEPLOYMENT"):
        _build_worklog_catalog(
            _jira(enable_jira_worklog=True, deployment="klaud"), _github(), _EVENTS
        )


def test_unimplemented_author_strategy_is_rejected_at_wiring() -> None:
    """``JiraSettings.validate`` woła tylko poller — te drzwi muszą sprawdzić same."""
    with pytest.raises(ValueError, match="SZKIELETEM"):
        _build_worklog_catalog(
            _jira(enable_jira_worklog=True, worklog_author_strategy="tempo"), _github(), _EVENTS
        )
