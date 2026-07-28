"""Wspólne atrapy i dane dla testów.

Atrapy implementują porty (``NotesRepository`` / ``ProjectsRepository``)
strukturalnie — bez dziedziczenia — dzięki czemu serwisy testujemy w pełni
w pamięci, bez dotykania dysku.
"""

from __future__ import annotations

from datetime import date

import pytest

from workmate.core.domain.models import (
    Note,
    NoteMetadata,
    Project,
    ProjectStatusRecord,
)

# --- Tymczasowa DEZAKTYWACJA testów wg ISTOTNOŚCI (decyzja operatora 2026-07-27) --------------
# Kryterium: aktywne zostają WYŁĄCZNIE testy niezbędne dla całego projektu + testy bieżącej,
# niezmergowanej pracy (A′1–A′3). Cała reszta (105 plików, ~1378 przypadków) jest POMIJANA PRZY
# KOLEKCJI — pliki zostają na dysku NIETKNIĘTE, to nie usunięcie. W pełni odwracalne: wyczyść
# ``collect_ignore`` (lub usuń wybrane wpisy), a pytest znów je zbierze. Ścieżki są WZGLĘDNE
# wobec tego katalogu (``tests/``).
#
# AKTYWNE — niezbędne guardy (bezpieczeństwo + bramki + zamrożone kontrakty):
#   security/test_{injection,path_traversal,secret_leakage}.py (kontrakty bezpieczeństwa),
#   adapters/test_mcp_tool_surface.py (golden — zamrożona powierzchnia MCP, Gate 1),
#   test_gates_closed_by_default.py (bramki zapisu OFF, ADR 0006),
#   adapters/test_tools_gating.py (bramkowanie zapisu, Gate 2),
#   core/test_tool_catalog.py (jednoźródłowy katalog narzędzi),
#   core/test_notes_domain.py (zamrożony kontrakt NoteMetadata, Gate 1).
# AKTYWNE — bieżąca praca A′1–A′3 (odpowiedź plikiem 0026 + push obrazu 0027):
#   adapters/test_document_renderer.py, adapters/test_graph_user_push.py,
#   adapters/test_conversational_responder_user_push.py, core/test_file_reply_tools.py,
#   core/test_user_image_push_tools.py, oraz w adapters/inbound/teams_graph/:
#   test_handler.py, test_settings.py, test_thread_tool_factory.py.
# AKTYWNE — bieżąca praca A′4 (dostawa worklogu ZAŁĄCZNIKIEM, ADR 0035/0038 przez 0027):
#   adapters/test_graph_user_doc_push.py (już aktywny), core/test_weekly_timesheets.py,
#   core/test_selfservice_worklog.py, core/domain/test_timesheet_message.py.
collect_ignore = [
    "adapters/inbound/cli/test_meeting.py",
    "adapters/inbound/github/test_poller.py",
    "adapters/inbound/github/test_selection.py",
    "adapters/inbound/jira/test_app.py",
    "adapters/inbound/jira/test_cloud_integration.py",
    "adapters/inbound/jira/test_poller.py",
    "adapters/inbound/jira/test_selection.py",
    "adapters/inbound/teams_graph/test_attachments.py",
    "adapters/inbound/teams_graph/test_formatting.py",
    "adapters/inbound/teams_graph/test_graph.py",
    "adapters/inbound/teams_graph/test_jira_catalog_wiring.py",
    "adapters/inbound/teams_graph/test_poller.py",
    "adapters/inbound/teams_graph/test_selection.py",
    "adapters/inbound/teams_graph/test_state.py",
    "adapters/inbound/teams_graph/test_worklog_catalog_wiring.py",
    "adapters/inbound/test_agent_wiring.py",
    "adapters/inbound/test_commands.py",
    "adapters/inbound/test_worklog_selfservice_state.py",
    "adapters/inbound/test_worklogi_door.py",
    "adapters/teams/test_handler.py",
    "adapters/teams/test_responder.py",
    "adapters/teams/test_runtime_responder.py",
    "adapters/teams/test_settings.py",
    "adapters/telegram/test_handler.py",
    "adapters/telegram/test_settings.py",
    "adapters/test_anthropic_llm.py",
    "adapters/test_anthropic_summarizer.py",
    "adapters/test_auth.py",
    "adapters/test_claude_summary_store.py",
    "adapters/test_cli_app.py",
    "adapters/test_conversational_responder.py",
    "adapters/test_conversational_responder_thread_tools.py",
    "adapters/test_env.py",
    "adapters/test_github_api.py",
    "adapters/test_github_commit_source.py",
    "adapters/test_github_commits.py",
    "adapters/test_graph_file_sender.py",
    "adapters/test_graph_teams_notifier.py",
    "adapters/test_jira_api.py",
    "adapters/test_jira_cloud_api.py",
    "adapters/test_jira_factory.py",
    "adapters/test_markdown_notes_repo.py",
    "adapters/test_markdown_notes_writer.py",
    "adapters/test_onnx_semantic_ranker.py",
    "adapters/test_safe_responder.py",
    "adapters/test_sqlite_conversations.py",
    "adapters/test_sqlite_events.py",
    "adapters/test_sqlite_thread_links.py",
    "adapters/test_tools_read.py",
    "adapters/test_worklogi_adapters.py",
    "adapters/test_workspace_filesystem.py",
    "adapters/test_yaml_projects_repo.py",
    "core/application/test_ci_autocomment.py",
    "core/application/test_search_hybrid.py",
    "core/domain/test_adf.py",
    "core/domain/test_ci.py",
    "core/domain/test_day_comment.py",
    "core/domain/test_issue_attribution.py",
    "core/domain/test_ranking.py",
    "core/domain/test_shift_hours.py",
    "core/domain/test_submitted_summary.py",
    "core/domain/test_threads.py",
    "core/domain/test_timesheet.py",
    "core/domain/test_timesheet_sheet.py",
    "core/domain/test_week.py",
    "core/domain/test_worklog_domain.py",
    "core/test_agent_runtime.py",
    "core/test_compaction.py",
    "core/test_conversations.py",
    "core/test_event_dimension.py",
    "core/test_events.py",
    "core/test_events_since_tool.py",
    "core/test_github_write_service.py",
    "core/test_jira_transition_catalog.py",
    "core/test_jira_transition_service.py",
    "core/test_jira_write_service.py",
    "core/test_llm_ports.py",
    "core/test_meeting_notes.py",
    "core/test_notifier.py",
    "core/test_paths.py",
    "core/test_pricing.py",
    "core/test_services.py",
    "core/test_shift_hours_source.py",
    "core/test_thread_reply_catalog.py",
    "core/test_tools_bridge.py",
    "core/test_worklog_catalog.py",
    "core/test_worklog_service.py",
    "core/test_workspace_domain.py",
    "core/test_workspace_service.py",
    "core/test_workspace_tools.py",
    "core/test_write_service.py",
    "test_agent_settings.py",
    "test_bridge_connectivity.py",
    "test_conversation_settings.py",
    "test_events_settings.py",
    "test_github_settings.py",
    "test_jira_settings.py",
    "test_retrieval_eval.py",
    "test_retrieval_settings.py",
    "test_server.py",
    "test_teams_push_settings.py",
    "test_workspace_settings.py",
]


def make_note(
    note_id: str,
    *,
    project: str,
    title: str,
    on: date,
    participants: list[str] | None = None,
    body: str = "",
    action_items: list[str] | None = None,
    tags: list[str] | None = None,
) -> Note:
    return Note(
        id=note_id,
        metadata=NoteMetadata(
            title=title,
            project=project,
            date=on,
            participants=participants or [],
            action_items=action_items or [],
            tags=tags or [],
        ),
        body=body,
    )


class FakeNotesRepository:
    """Atrapa ``NotesRepository`` trzymająca notatki w liście."""

    def __init__(self, notes: list[Note]) -> None:
        self._notes = notes

    def all(self) -> list[Note]:
        return list(self._notes)

    def get(self, note_id: str) -> Note | None:
        return next((n for n in self._notes if n.id == note_id), None)


class FakeProjectsRepository:
    """Atrapa ``ProjectsRepository`` trzymająca projekty i statusy w mapach."""

    def __init__(
        self,
        projects: list[Project],
        records: dict[str, ProjectStatusRecord],
    ) -> None:
        self._projects = projects
        self._records = records

    def all(self) -> list[Project]:
        return list(self._projects)

    def get(self, key: str) -> Project | None:
        return next((p for p in self._projects if p.key == key), None)

    def status_record(self, key: str) -> ProjectStatusRecord | None:
        return self._records.get(key)


class FakeNotesWriter:
    """Atrapa ``NotesWriter`` trzymająca zapisane notatki w mapie id → Note."""

    def __init__(self) -> None:
        self.saved: dict[str, Note] = {}

    def exists(self, note_id: str) -> bool:
        return note_id in self.saved

    def write(self, note: Note) -> None:
        self.saved[note.id] = note


@pytest.fixture
def sample_notes() -> list[Note]:
    return [
        make_note(
            "mpwik/scada-integration/2025-06-12-api",
            project="scada-integration",
            title="Przegląd kontraktu API",
            on=date(2025, 6, 12),
            participants=["Anna Kowalska", "Marek Nowak"],
            body="Domknęliśmy kontrakt API oparty o wąskie endpointy.",
            action_items=["Wdrożyć walidację wejścia", "Przygotować checklistę"],
            tags=["api", "bezpieczenstwo"],
        ),
        make_note(
            "mpwik/scada-integration/2025-05-14-kickoff",
            project="scada-integration",
            title="Kickoff integracji",
            on=date(2025, 5, 14),
            participants=["Anna Kowalska"],
            body="Zakres MVP to odczyt danych ze SCADA przez API.",
            action_items=["Szkic API"],
            tags=["kickoff", "api"],
        ),
        make_note(
            "biap/workmate/2025-06-10-schemat",
            project="workmate",
            title="Schemat notatki",
            on=date(2025, 6, 10),
            participants=["Piotr Zieliński"],
            body="Zablokowaliśmy schemat notatki i kontrakt narzędzi.",
            action_items=[],
            tags=["schemat"],
        ),
    ]
