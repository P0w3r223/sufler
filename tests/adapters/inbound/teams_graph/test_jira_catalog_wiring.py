"""Testy wiringu _build_jira_catalog (ADR 0031/0032) — dwie NIEZALEŻNE bramki Jiry na drzwiach.

Sedno topologii bramek: zapis (create/comment) wchodzi tylko przy ``enable_jira_write``, tranzycja
tylko przy ``enable_jira_transition`` — niezależnie. Żadna bramka → pusty katalog (agent bez
narzędzi mutujących Jira). Bramka ON bez celu (token/URL/projekt) → TWARDY błąd (cicha „martwa"
bramka to footgun). Golden-test powierzchni MCP (test_mcp_tool_surface) pozostaje nietknięty — te
narzędzia wchodzą przez ``extra_catalog``, nie przez ``build_tool_catalog``.
"""

from __future__ import annotations

import pytest

from workmate.adapters.inbound.teams_graph.app import _build_bridge_catalog, _build_jira_catalog
from workmate.config import EventsSettings, GithubSettings, JiraSettings

# ``_build_jira_catalog`` nie używa magazynu zdarzeń podczas budowy katalogu (tylko wstrzykuje go
# do serwisu), więc None wystarcza — bramkowanie zależy wyłącznie od flag JiraSettings.
_EVENTS = None


def _settings(**kw) -> JiraSettings:
    base: dict = {
        "base_url": "https://jira.example.com",
        "token": "PAT",
        "watch_projects": ("WM",),
        "write_project": "WM",
        "self_account": "svc",
    }
    base.update(kw)
    return JiraSettings(**base)


def _names(settings: JiraSettings) -> set[str]:
    return {s.name for s in _build_jira_catalog(settings, _EVENTS)}


def test_no_gate_yields_empty_catalog():
    assert _build_jira_catalog(_settings(), _EVENTS) == []


def test_bridge_catalog_enforces_jira_limits_before_touching_the_store(tmp_path):
    """Sufity Jiry egzekwuje TEŻ proces, który wykonuje zapis — nie tylko poller.

    Regresja: ``JiraSettings.validate()`` woła wyłącznie ``workmate-jira``, więc absurd
    w ``.env`` (tu: sufit 100 h na wpis przy backstopie 24 h) przechodził w drzwiach
    Teams — czyli dokładnie tam, gdzie zapis się odbywa. Błąd musi paść PRZED dotknięciem
    magazynu zdarzeń, stąd ścieżka bazy prowadzi do nieistniejącego katalogu.
    """
    with pytest.raises(ValueError, match="WORKMATE_JIRA_WORKLOG_MAX_HOURS"):
        _build_bridge_catalog(
            EventsSettings(db_path=tmp_path / "nie-ma-katalogu" / "events.db"),
            GithubSettings(),
            _settings(worklog_max_hours_per_entry=100.0),
        )


def test_bridge_catalog_accepts_deployment_without_jira_configured(tmp_path):
    """Wdrożenie bez Jiry ma startować — ``validate_limits`` nie żąda URL-a ani tokenu."""
    catalog, factory = _build_bridge_catalog(
        EventsSettings(db_path=tmp_path / "events.db"), GithubSettings(), JiraSettings()
    )

    assert factory is None
    assert {"read_recent_events"} <= {spec.name for spec in catalog}


def test_write_only_yields_create_and_comment_tools():
    assert _names(_settings(enable_jira_write=True)) == {
        "create_jira_issue",
        "comment_jira_issue",
    }


def test_transition_only_yields_transition_tool():
    # Profil „tylko-tranzycja": tranzycja bez włączonego zapisu (bramki niezależne).
    assert _names(_settings(enable_jira_transition=True)) == {"transition_jira_issue"}


def test_both_gates_yield_all_three_tools():
    names = _names(_settings(enable_jira_write=True, enable_jira_transition=True))
    assert names == {"create_jira_issue", "comment_jira_issue", "transition_jira_issue"}


def test_gate_on_without_write_project_raises():
    with pytest.raises(ValueError, match="ENABLE"):
        _build_jira_catalog(_settings(enable_jira_transition=True, write_project=""), _EVENTS)


def test_transition_gate_rejects_out_of_range_hop_cap():
    # Twardy backstop sufitu hopów (ADR 0032) egzekwowany w drzwiach wykonujących walk — poza
    # zasięgiem JiraSettings.validate (woła go tylko poller). Absurdalny cap → fail-fast, nie clamp.
    with pytest.raises(ValueError, match="MAX_TRANSITION_HOPS"):
        _build_jira_catalog(
            _settings(enable_jira_transition=True, max_transition_hops=999), _EVENTS
        )


def test_transition_gate_accepts_hop_cap_at_ceiling():
    # Wartość na suficie (10) jest dozwolona — walk wielo-hop działa; tool się buduje.
    names = _names(_settings(enable_jira_transition=True, max_transition_hops=10))
    assert names == {"transition_jira_issue"}


# --- ścieżka Cloud (ADR 0033) — Basic auth wymaga e-maila -------------------


def test_cloud_gate_without_email_raises():
    # Na Cloud brak WORKMATE_JIRA_EMAIL = niedziałające Basic auth → fail-fast (jak brak projektu).
    with pytest.raises(ValueError, match="EMAIL"):
        _build_jira_catalog(
            _settings(enable_jira_write=True, deployment="cloud", email=""), _EVENTS
        )


def test_cloud_gate_with_email_builds_catalog():
    names = _names(_settings(enable_jira_write=True, deployment="cloud", email="me@example.com"))
    assert names == {"create_jira_issue", "comment_jira_issue"}


def test_gate_rejects_unknown_deployment():
    # Literówka w DEPLOYMENT nie może po cichu zbudować klienta Server/DC na Cloud — fail-fast
    # spójny z pollerem (JiraSettings.validate). Inaczej cicha, martwa konfiguracja (→ 401 runtime).
    with pytest.raises(ValueError, match="DEPLOYMENT"):
        _build_jira_catalog(_settings(enable_jira_write=True, deployment="cloudd"), _EVENTS)
