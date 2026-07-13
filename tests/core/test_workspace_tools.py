"""Testy narzędzi katalogu roboczego agenta (ADR 0018): build_workspace_catalog.

Powierzchnia MCP (4+1 notatki) jest zamrożona osobnym golden-testem ``test_mcp_tool_surface`` —
narzędzia workspace są WYŁĄCZNIE dla runtime agenta, więc tam się nie pojawiają (gdyby się
pojawiły, golden-test by padł). Tu sprawdzamy same narzędzia workspace i kopertę błędów.
"""
from __future__ import annotations

from workmate.core.application.tools import build_workspace_catalog
from workmate.core.application.workspace import (
    WorkspaceLimits,
    WorkspaceService,
    WorkspaceWriteService,
)
from workmate.core.domain.workspace import WorkspaceFile, WorkspaceScope

_SCOPE = WorkspaceScope("teams_graph", "team/chan/root")


class _FakeWorkspace:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    def exists(self, relpath: str) -> bool:
        return relpath in self.files

    def create(self, relpath: str, content: str) -> WorkspaceFile:
        self.files[relpath] = content
        return WorkspaceFile(relpath.rsplit("/", 1)[-1], relpath, len(content.encode("utf-8")))

    def list(self, scope_dir: str) -> list[WorkspaceFile]:
        return [
            WorkspaceFile(rp.rsplit("/", 1)[-1], rp, len(c.encode("utf-8")))
            for rp, c in self.files.items()
            if rp.startswith(f"{scope_dir}/")
        ]

    def read(self, scope_dir: str, name: str) -> str | None:
        return self.files.get(f"{scope_dir}/{name}")


def _catalog():
    fake = _FakeWorkspace()
    limits = WorkspaceLimits(
        max_file_bytes=1_000_000,
        max_files_per_scope=50,
        max_total_bytes=5_000_000,
        allowed_ext=frozenset({"md", "txt"}),
    )
    tools = build_workspace_catalog(
        _SCOPE, WorkspaceService(fake), WorkspaceWriteService(fake, fake, limits)
    )
    return {spec.name: spec.fn for spec in tools}, fake


def test_catalog_exposes_three_workspace_tools():
    tools, _ = _catalog()
    assert set(tools) == {"create_file", "read_file", "list_files"}


def test_create_file_tool_creates_and_reports_path():
    tools, fake = _catalog()

    result = tools["create_file"](name="raport.md", content="treść")

    assert result == {
        "created": True,
        "name": "raport.md",
        "path": f"{_SCOPE.dirpath()}/raport.md",
    }
    assert fake.files[f"{_SCOPE.dirpath()}/raport.md"] == "treść"


def test_create_file_tool_bad_extension_returns_error_envelope():
    tools, _ = _catalog()

    result = tools["create_file"](name="skrypt.exe", content="echo")

    assert "error" in result and "created" not in result  # koperta: WriteError → {"error": ...}


def test_read_and_list_file_tools():
    tools, _ = _catalog()
    tools["create_file"](name="raport.md", content="pełna treść")

    assert tools["read_file"](name="raport.md") == {"name": "raport.md", "content": "pełna treść"}
    assert "error" in tools["read_file"](name="brak.md")
    listing = tools["list_files"]()
    assert listing["count"] == 1
    assert listing["files"] == [{"name": "raport.md", "size": len("pełna treść".encode())}]
