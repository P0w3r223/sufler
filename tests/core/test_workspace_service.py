"""Testy serwisów katalogu roboczego (ADR 0018) na atrapie in-memory — bez dysku.

Sedno: create-only z sufiksem, kwoty (rozmiar pliku / liczba / łączny rozmiar per rozmowa),
guard znaków sterujących, biała lista rozszerzeń oraz ochrona odczytu przed „..".
"""

from __future__ import annotations

import pytest

from workmate.core.application.workspace import (
    WorkspaceLimits,
    WorkspaceService,
    WorkspaceWriteService,
)
from workmate.core.domain.workspace import WorkspaceFile, WorkspaceScope
from workmate.core.errors import WriteError

_SCOPE = WorkspaceScope("teams_graph", "team/chan/root")


class _FakeWorkspace:
    """Atrapa portów ``WorkspaceRepository`` + ``WorkspaceWriter`` — dict ``relpath → treść``."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    # --- WorkspaceWriter ---
    def exists(self, relpath: str) -> bool:
        return relpath in self.files

    def create(self, relpath: str, content: str) -> WorkspaceFile:
        self.files[relpath] = content
        return WorkspaceFile(
            name=relpath.rsplit("/", 1)[-1], relpath=relpath, size=len(content.encode("utf-8"))
        )

    # --- WorkspaceRepository ---
    def list(self, scope_dir: str) -> list[WorkspaceFile]:
        return [
            WorkspaceFile(rp.rsplit("/", 1)[-1], rp, len(c.encode("utf-8")))
            for rp, c in self.files.items()
            if rp.startswith(f"{scope_dir}/")
        ]

    def read(self, scope_dir: str, name: str) -> str | None:
        return self.files.get(f"{scope_dir}/{name}")


def _limits(
    *, max_file_bytes: int = 1_000_000, max_files: int = 50, max_total: int = 5_000_000
) -> WorkspaceLimits:
    return WorkspaceLimits(
        max_file_bytes=max_file_bytes,
        max_files_per_scope=max_files,
        max_total_bytes=max_total,
        allowed_ext=frozenset({"md", "txt", "csv", "json"}),
    )


def test_create_file_writes_and_returns_descriptor():
    fake = _FakeWorkspace()
    svc = WorkspaceWriteService(fake, fake, _limits())

    created = svc.create_file(_SCOPE, "raport.md", "treść")

    assert created.name == "raport.md"
    assert created.relpath == f"{_SCOPE.dirpath()}/raport.md"
    assert fake.files[created.relpath] == "treść"


def test_create_file_never_overwrites_appends_suffix():
    fake = _FakeWorkspace()
    svc = WorkspaceWriteService(fake, fake, _limits())

    first = svc.create_file(_SCOPE, "raport.md", "a")
    second = svc.create_file(_SCOPE, "raport.md", "b")

    assert first.name == "raport.md"
    assert second.name == "raport-2.md"  # kolizja → sufiks, oryginał nietknięty
    assert fake.files[first.relpath] == "a"


def test_create_file_rejects_oversized_file():
    fake = _FakeWorkspace()
    svc = WorkspaceWriteService(fake, fake, _limits(max_file_bytes=10))

    with pytest.raises(WriteError, match="limit rozmiaru"):
        svc.create_file(_SCOPE, "duzy.md", "x" * 100)


def test_create_file_rejects_over_file_count():
    fake = _FakeWorkspace()
    svc = WorkspaceWriteService(fake, fake, _limits(max_files=1))

    svc.create_file(_SCOPE, "a.md", "1")
    with pytest.raises(WriteError, match="limit liczby plików"):
        svc.create_file(_SCOPE, "b.md", "2")


def test_create_file_rejects_over_total_budget():
    fake = _FakeWorkspace()
    svc = WorkspaceWriteService(fake, fake, _limits(max_total=150))

    svc.create_file(_SCOPE, "a.md", "x" * 100)  # 100 B — mieści się
    with pytest.raises(WriteError, match="łączny limit"):
        svc.create_file(_SCOPE, "b.md", "y" * 100)  # 100 B — przekracza łącznie 150


def test_create_file_rejects_control_chars_in_content():
    fake = _FakeWorkspace()
    svc = WorkspaceWriteService(fake, fake, _limits())

    with pytest.raises(WriteError):
        svc.create_file(_SCOPE, "raport.md", "treść\x00zerowa")


def test_create_file_rejects_disallowed_extension():
    fake = _FakeWorkspace()
    svc = WorkspaceWriteService(fake, fake, _limits())

    with pytest.raises(WriteError):
        svc.create_file(_SCOPE, "skrypt.exe", "echo")


def test_read_and_list_within_scope():
    fake = _FakeWorkspace()
    write = WorkspaceWriteService(fake, fake, _limits())
    read = WorkspaceService(fake)
    write.create_file(_SCOPE, "raport.md", "treść raportu")

    assert read.read_file(_SCOPE, "raport.md") == "treść raportu"
    assert read.read_file(_SCOPE, "brak.md") is None
    listing = read.list_files(_SCOPE)
    assert [f.name for f in listing] == ["raport.md"]


def test_read_rejects_traversal_name():
    read = WorkspaceService(_FakeWorkspace())
    with pytest.raises(WriteError):
        read.read_file(_SCOPE, "../../secret.env")


def test_scopes_do_not_leak_files_between_conversations():
    fake = _FakeWorkspace()
    write = WorkspaceWriteService(fake, fake, _limits())
    read = WorkspaceService(fake)
    other = WorkspaceScope("teams_graph", "team/chan/INNY")
    write.create_file(_SCOPE, "moj.md", "x")

    assert read.list_files(other) == []  # inna rozmowa nie widzi plików tej rozmowy
