"""Centralna, typowana konfiguracja serwera.

Wszystkie wartości pochodzą ze zmiennych środowiskowych (patrz ``.env.example``)
i mają sensowne wartości domyślne wyznaczane względem korzenia repozytorium.
Dzięki temu ``python -m sufler`` działa bez żadnej konfiguracji, a wdrożenie
może nadpisać ścieżki pojedynczą zmienną środowiskową.

Pakiet, nie moduł: jeden plik na domenę ustawień, wspólne pomocniki w ``_env``.
Ten plik jest JEDYNYM wejściem — ``from sufler.config import <cokolwiek>`` działa
jak przed rozbiciem, a sięganie po moduł domeny wprost łamie kontrakt import-lintera
„config ma jedno wejście" (``pyproject.toml``), więc nie jest to prośba, tylko bramka.
"""

from __future__ import annotations

from sufler.config._env import (
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
from sufler.config.agent import AgentSettings
from sufler.config.conversation import ConversationSettings
from sufler.config.events import EventsSettings
from sufler.config.exec_manager import ExecManagerSettings
from sufler.config.github import MAX_WORKLOG_RANGE_DAYS, GithubSettings
from sufler.config.jira import JIRA_DEPLOYMENTS, JiraSettings
from sufler.config.retrieval import RetrievalSettings
from sufler.config.schedule import ScheduleSettings
from sufler.config.server import _ALLOWED_LOG_LEVELS, _DEFAULT_TOKENS_FILE, Settings
from sufler.config.shell import ShellSettings
from sufler.config.skills import SkillsSettings
from sufler.config.teams import TeamsSettings
from sufler.config.teams_digest import MAX_TEAMS_DIGEST_CATCHUP_DAYS, TeamsDigestSettings
from sufler.config.teams_graph import TeamsGraphSettings
from sufler.config.teams_push import TeamsPushSettings
from sufler.config.workspace import WorkspaceSettings

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
