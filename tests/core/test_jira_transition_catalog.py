"""Testy build_jira_transition_catalog (ADR 0032) — pojedyncze narzędzie tranzycji Jiry.

Narzędzie wchodzi jako ``extra_catalog`` (osobne od create-only ``build_jira_write_catalog``).
Sedno: jedno narzędzie ``transition_jira_issue(issue_key, target_status)`` zwracające STRUKTURALNY
raport walk (nie ``{"created": True}``), z kopertą błędów pre-flight (WriteError → ``error``).
Testujemy na realnym ``JiraWriteService`` nad atrapą portu (jak test_thread_reply_catalog).
"""

from __future__ import annotations

import inspect

from workmate.core.application.jira import JiraWriteService
from workmate.core.application.tools import build_jira_transition_catalog

_REPORT_KEYS = {
    "transitioned",
    "reached",
    "status",
    "path",
    "hops",
    "stop_reason",
    "available_next",
}


class _Writer:
    """Atrapa ``JiraWritePort`` — cel jest bezpośrednim sąsiadem (single-hop reached)."""

    def read_transitions(self, issue_key):
        return {
            "current_status": "To Do",
            "transitions": [{"id": "11", "name": "Start", "to_status": "In Progress"}],
        }

    def transition_issue(self, issue_key, transition_id):
        return {
            "url": f"https://j/browse/{issue_key}",
            "status": "In Progress",
            "updated": "2026-07-15T10:00:00.000+0200",
        }


def _svc(writer=None, *, project="WM") -> JiraWriteService:
    return JiraWriteService(writer or _Writer(), project=project)


def test_catalog_exposes_single_transition_tool():
    catalog = build_jira_transition_catalog(_svc())
    assert [s.name for s in catalog] == ["transition_jira_issue"]


def test_tool_signature_takes_issue_key_and_target_status():
    catalog = build_jira_transition_catalog(_svc())
    params = list(inspect.signature(catalog[0].fn).parameters)
    assert params == ["issue_key", "target_status"]


def test_tool_returns_structured_walk_report_not_created_flag():
    catalog = build_jira_transition_catalog(_svc())

    result = catalog[0].fn(issue_key="WM-5", target_status="In Progress")

    assert result["reached"] is True
    assert result["stop_reason"] is None
    assert "created" not in result  # tranzycja oddaje raport, nie {"created": True}
    assert set(result) >= _REPORT_KEYS


def test_tool_envelopes_pre_flight_write_error():
    # Zły klucz (obcy projekt) → WriteError w pre-flight → koperta z ``error``, nie wyjątek.
    catalog = build_jira_transition_catalog(_svc(project="WM"))

    result = catalog[0].fn(issue_key="OPS-1", target_status="Done")

    assert "error" in result
    assert "spoza skonfigurowanego" in result["error"]


def test_description_carries_write_keyword_and_explicit_rule():
    spec = build_jira_transition_catalog(_svc())[0]
    assert "ZAPIS" in spec.description  # sygnał mutacji
    assert "WPROST" in spec.description  # tylko na jawną prośbę
    assert "stop_reason" in spec.description  # instrukcja zrelacjonowania raportu
