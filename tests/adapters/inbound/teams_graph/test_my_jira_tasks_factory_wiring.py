"""Testy wiringu ``_build_my_jira_tasks_factory`` (ADR 0054) — brama konfiguracji + tożsamość.

Sedno: fabryka istnieje TYLKO gdy Jira (odczyt) i mapa tożsamości są skonfigurowane; per nadawca
zwraca narzędzie TYLKO gdy tożsamość rozwiązuje się do konta Jira (fail-closed, zero domysłów).
Sieci tu nie ma — sprawdzamy wyłącznie strukturę wiringu, nie realny odczyt Jiry (patrz testy
``MyJiraTasksService``/katalogu).
"""

from __future__ import annotations

from sufler.adapters.inbound.teams_graph.app import _build_my_jira_tasks_factory
from sufler.config import JiraSettings, TeamsGraphSettings

_JIRA = JiraSettings(base_url="https://jira.example.org", token="pat-secret")


def _identities_file(tmp_path, *, aad_user_id="aad-123", jira_user="mikolaj@example.org"):
    """``jira_user=None`` pomija pole — wpis „tylko Teams" (ADR 0070 §1)."""
    path = tmp_path / "identities.yaml"
    wpis = f"EMP-1:\n  aad_user_id: {aad_user_id}\n"
    if jira_user is not None:
        wpis += f"  jira_user: {jira_user}\n"
    path.write_text(wpis, encoding="utf-8")
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


def test_known_sender_gets_the_tool(tmp_path) -> None:
    """Krok 5.3 (ADR 0009 paczki): sześć dawnych narzędzi Jiry to jedno ``Jira(action=…)``.

    Fabryka wciąż buduje serwis DOMKNIĘTY na koncie nadawcy (ADR 0054) — zmienia się tylko to,
    ile ``ToolSpec``-ów z niego wychodzi. Same akcje i inwariant „``my_*`` nie czyta ``member``"
    sondujemy w ``tests/core/test_jira_catalog.py``, bo tam jest builder.
    """
    settings = TeamsGraphSettings(
        meeting_note_identities=_identities_file(tmp_path, aad_user_id="aad-123")
    )
    factory = _build_my_jira_tasks_factory(settings, _JIRA)
    assert factory is not None
    tools = factory("aad-123")
    assert [t.name for t in tools] == ["Jira"]


def test_sender_mapped_to_a_different_person_gets_no_tool(tmp_path) -> None:
    """Mapa zna KOGOŚ, ale nie tego nadawcę — fail-closed, nie dopasowanie po najbliższym."""
    identities = _identities_file(tmp_path, aad_user_id="aad-999", jira_user="kolega@example.org")
    settings = TeamsGraphSettings(meeting_note_identities=identities)
    factory = _build_my_jira_tasks_factory(settings, _JIRA)
    assert factory is not None
    # Sender inny niż zmapowany — fail-closed, brak narzędzia.
    assert factory("aad-not-mapped") == []


def test_mapped_sender_without_a_jira_account_gets_no_tool(tmp_path) -> None:
    """Druga strona ADR 0070 §3: pełne członkostwo NIE znaczy pełnej powierzchni narzędzi.

    Osoba bez konta Jira nie dostaje ``Jira`` z pustym ``assignee`` — nie dostaje go WCALE, więc
    traci też ``task``, ``search`` i pytania o zadania INNYCH ludzi, które z jej własnym brakiem
    konta nie mają nic wspólnego. To konsekwencja braku konta, nie mapy; ADR nazywa ją wprost,
    żeby nie została później odkryta jako usterka.

    Do 2026-09-04 ta ścieżka była NIEOSIĄGALNA — ładowarka nie wpuszczała pustego ``jira_user``,
    więc straż ``person.jira_user`` w fabryce stała nieprzetestowana. Dziś jest jedyną rzeczą
    między „bez konta" a serwisem domkniętym na pustym koncie, czyli listą zadań, która
    milcząco nie jest niczyja.
    """
    mapa = _identities_file(tmp_path, jira_user=None)
    settings = TeamsGraphSettings(meeting_note_identities=mapa)
    factory = _build_my_jira_tasks_factory(settings, _JIRA)

    assert factory is not None  # sama fabryka powstaje — mapa jest poprawna
    assert factory("aad-123") == []
