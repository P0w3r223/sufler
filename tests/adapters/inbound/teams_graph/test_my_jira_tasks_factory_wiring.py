"""Testy wiringu ``_build_my_jira_tasks_factory`` (ADR 0054) — brama konfiguracji + tożsamość.

Sedno: fabryka istnieje TYLKO gdy Jira (odczyt) i mapa tożsamości są skonfigurowane; per nadawca
zwraca narzędzie TYLKO gdy tożsamość rozwiązuje się do konta Jira (fail-closed, zero domysłów).
Sieci tu nie ma — sprawdzamy wyłącznie strukturę wiringu, nie realny odczyt Jiry (patrz testy
``MyJiraTasksService``/katalogu).
"""

from __future__ import annotations

from workmate.adapters.inbound.teams_graph.app import _build_my_jira_tasks_factory
from workmate.config import JiraSettings, TeamsGraphSettings

_JIRA = JiraSettings(base_url="https://jira.example.org", token="pat-secret")


def _identities_file(tmp_path, *, aad_user_id="aad-123", jira_user="mikolaj@example.org"):
    path = tmp_path / "identities.yaml"
    path.write_text(
        f"EMP-1:\n  aad_user_id: {aad_user_id}\n  jira_user: {jira_user}\n",
        encoding="utf-8",
    )
    return path


def test_none_when_jira_read_not_configured(tmp_path) -> None:
    settings = TeamsGraphSettings(meeting_note_identities=_identities_file(tmp_path))
    assert _build_my_jira_tasks_factory(settings, JiraSettings()) is None


def test_none_when_identities_file_missing(tmp_path) -> None:
    settings = TeamsGraphSettings(meeting_note_identities=tmp_path / "brak.yaml")
    assert _build_my_jira_tasks_factory(settings, _JIRA) is None


def test_factory_present_when_both_configured(tmp_path) -> None:
    settings = TeamsGraphSettings(meeting_note_identities=_identities_file(tmp_path))
    assert _build_my_jira_tasks_factory(settings, _JIRA) is not None


def test_unknown_sender_gets_no_tool_fail_closed(tmp_path) -> None:
    settings = TeamsGraphSettings(meeting_note_identities=_identities_file(tmp_path))
    factory = _build_my_jira_tasks_factory(settings, _JIRA)
    assert factory is not None
    assert factory("nieznany-aad-id") == []


def test_empty_sender_gets_no_tool(tmp_path) -> None:
    settings = TeamsGraphSettings(meeting_note_identities=_identities_file(tmp_path))
    factory = _build_my_jira_tasks_factory(settings, _JIRA)
    assert factory is not None
    assert factory("") == []


def test_known_sender_gets_the_tools(tmp_path) -> None:
    """ADR 0056: fabryka zwraca teraz "moje zadania"/historia + rozszerzony odczyt Jiry (razem)."""
    settings = TeamsGraphSettings(
        meeting_note_identities=_identities_file(tmp_path, aad_user_id="aad-123")
    )
    factory = _build_my_jira_tasks_factory(settings, _JIRA)
    assert factory is not None
    tools = factory("aad-123")
    assert {t.name for t in tools} == {
        "get_my_jira_tasks",
        "get_my_jira_history",
        "get_jira_task",
        "search_jira_tasks",
        "get_member_jira_tasks",
        "get_member_jira_history",
    }


def test_sender_mapped_to_a_different_person_gets_no_tool(tmp_path) -> None:
    """Mapa zna KOGOŚ, ale nie tego nadawcę — fail-closed, nie dopasowanie po najbliższym."""
    identities = _identities_file(tmp_path, aad_user_id="aad-999", jira_user="kolega@example.org")
    settings = TeamsGraphSettings(meeting_note_identities=identities)
    factory = _build_my_jira_tasks_factory(settings, _JIRA)
    assert factory is not None
    # Sender inny niż zmapowany — fail-closed, brak narzędzia.
    assert factory("aad-not-mapped") == []
