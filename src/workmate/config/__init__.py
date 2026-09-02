"""Centralna, typowana konfiguracja serwera.

Wszystkie wartości pochodzą ze zmiennych środowiskowych (patrz ``.env.example``)
i mają sensowne wartości domyślne wyznaczane względem korzenia repozytorium.
Dzięki temu ``python -m workmate`` działa bez żadnej konfiguracji, a wdrożenie
może nadpisać ścieżki pojedynczą zmienną środowiskową.

Pakiet, nie moduł: jeden plik na domenę ustawień, wspólne pomocniki w ``_env``.
Ten plik jest JEDYNYM wejściem — ``from workmate.config import <cokolwiek>`` działa
jak przed rozbiciem i nowy kod ma sięgać tutaj, nie do modułów wewnętrznych.
"""

from __future__ import annotations

from workmate.config._env import (
    _bool_from_env,
    _find_repo_root,
    _float_from_env,
    _int_from_env,
    _list_from_env,
    _optional_path_from_env,
    _path_from_env,
    _repo_root_or_none,
    require_writable,
)
from workmate.config.agent import AgentSettings
from workmate.config.conversation import ConversationSettings
from workmate.config.events import EventsSettings
from workmate.config.exec_manager import ExecManagerSettings
from workmate.config.github import MAX_WORKLOG_RANGE_DAYS, GithubSettings
from workmate.config.jira import JIRA_DEPLOYMENTS, JiraSettings
from workmate.config.retrieval import RetrievalSettings
from workmate.config.schedule import ScheduleSettings
from workmate.config.server import _ALLOWED_LOG_LEVELS, _DEFAULT_TOKENS_FILE, Settings
from workmate.config.shell import ShellSettings
from workmate.config.skills import SkillsSettings
from workmate.config.teams import TeamsSettings
from workmate.config.teams_digest import MAX_TEAMS_DIGEST_CATCHUP_DAYS, TeamsDigestSettings
from workmate.config.teams_graph import TeamsGraphSettings
from workmate.config.teams_push import TeamsPushSettings
from workmate.config.workspace import WorkspaceSettings

# Nazwy z podkreśleniem w re-eksporcie NIE są przeoczeniem: `deploy/http/manage_tokens.py`
# bierze stąd `_DEFAULT_TOKENS_FILE` (jedno źródło ścieżki magazynu tokenów), a testy
# helperów env i poziomów logu sięgają po `_*_from_env` i `_ALLOWED_LOG_LEVELS`. Rozbicie
# na pakiet miało nie ruszyć ani jednego importu — więc nie rusza też tych.
__all__ = [
    "JIRA_DEPLOYMENTS",
    "MAX_TEAMS_DIGEST_CATCHUP_DAYS",
    "MAX_WORKLOG_RANGE_DAYS",
    "AgentSettings",
    "ConversationSettings",
    "EventsSettings",
    "ExecManagerSettings",
    "GithubSettings",
    "JiraSettings",
    "RetrievalSettings",
    "ScheduleSettings",
    "Settings",
    "ShellSettings",
    "SkillsSettings",
    "TeamsDigestSettings",
    "TeamsGraphSettings",
    "TeamsPushSettings",
    "TeamsSettings",
    "WorkspaceSettings",
    "_ALLOWED_LOG_LEVELS",
    "_DEFAULT_TOKENS_FILE",
    "_bool_from_env",
    "_find_repo_root",
    "_float_from_env",
    "_int_from_env",
    "_list_from_env",
    "_optional_path_from_env",
    "_path_from_env",
    "_repo_root_or_none",
    "require_writable",
]
