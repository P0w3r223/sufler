"""Fabryka katalogu roboczego agenta bez powłoki (``CreateFile``/``ReadFile``/``ListFiles``)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sufler.adapters.outbound.filesystem_workspace import (
    FilesystemWorkspaceRepository,
    FilesystemWorkspaceWriter,
)
from sufler.core.application.tools import (
    build_workspace_catalog,
)
from sufler.core.application.workspace import (
    WorkspaceLimits,
    WorkspaceService,
    WorkspaceWriteService,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sufler.config import (
        WorkspaceSettings,
    )
    from sufler.core.application.tools import ToolSpec
    from sufler.core.domain.workspace import WorkspaceScope

logger = logging.getLogger(__name__)


def _build_workspace_factory(
    workspace_settings: WorkspaceSettings,
) -> Callable[[WorkspaceScope], list[ToolSpec]]:
    """Fabryka narzędzi KATALOGU ROBOCZEGO (ADR 0018) wiążących je ze scope rozmowy.

    Serwisy (read/write) budujemy RAZ; fabryka na turę tylko domyka je scope'em rozmowy —
    izolacja per rozmowa bez współdzielonego stanu w runtime.
    """
    repo = FilesystemWorkspaceRepository(workspace_settings.workspace_dir)
    limits = WorkspaceLimits(
        max_file_bytes=workspace_settings.max_file_mb * 1024 * 1024,
        max_files_per_scope=workspace_settings.max_files_per_scope,
        max_total_bytes=workspace_settings.max_total_mb * 1024 * 1024,
        allowed_ext=frozenset(workspace_settings.allowed_ext),
    )
    read_service = WorkspaceService(repo)
    write_service = WorkspaceWriteService(
        FilesystemWorkspaceWriter(workspace_settings.workspace_dir), repo, limits
    )

    def factory(scope: WorkspaceScope) -> list[ToolSpec]:
        return build_workspace_catalog(scope, read_service, write_service)

    return factory
