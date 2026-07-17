"""Testy adaptera katalogu roboczego na dysku (ADR 0018) — create-only i ochrona path-traversal.

Realny FS (``tmp_path``): sedno to atomowy zapis create-only (``os.link``) oraz ``resolve()`` +
``relative_to(root)`` na każdej ścieżce (zapis i odczyt) — nic nie wyjdzie poza korzeń workspace.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from workmate.adapters.outbound.filesystem_workspace import (
    FilesystemWorkspaceRepository,
    FilesystemWorkspaceWriter,
    prune_stale,
)
from workmate.core.errors import WriteError


def test_create_writes_file_and_returns_descriptor(tmp_path):
    writer = FilesystemWorkspaceWriter(tmp_path)

    created = writer.create("teams_graph/abc/raport.md", "treść")

    assert (tmp_path / "teams_graph" / "abc" / "raport.md").read_text(encoding="utf-8") == "treść"
    assert created.name == "raport.md"
    assert created.size == len("treść".encode())


def test_create_is_create_only_collision_raises(tmp_path):
    writer = FilesystemWorkspaceWriter(tmp_path)
    writer.create("teams_graph/abc/raport.md", "pierwsza")

    with pytest.raises(WriteError, match="już istnieje"):
        writer.create("teams_graph/abc/raport.md", "druga")

    # Oryginał nietknięty (nigdy nie nadpisujemy).
    target = tmp_path / "teams_graph" / "abc" / "raport.md"
    assert target.read_text(encoding="utf-8") == "pierwsza"


def test_write_path_traversal_is_rejected(tmp_path):
    writer = FilesystemWorkspaceWriter(tmp_path)
    with pytest.raises(WriteError, match="poza katalogiem"):
        writer.create("../ucieczka.md", "x")


def test_list_and_read_within_scope(tmp_path):
    writer = FilesystemWorkspaceWriter(tmp_path)
    repo = FilesystemWorkspaceRepository(tmp_path)
    writer.create("teams_graph/abc/a.md", "AAA")
    writer.create("teams_graph/abc/b.txt", "BB")

    names = [f.name for f in repo.list("teams_graph/abc")]
    assert names == ["a.md", "b.txt"]  # posortowane, bez plików .tmp
    assert repo.read("teams_graph/abc", "a.md") == "AAA"
    assert repo.read("teams_graph/abc", "niema.md") is None


def test_list_missing_dir_is_empty(tmp_path):
    repo = FilesystemWorkspaceRepository(tmp_path)
    assert repo.list("teams_graph/nieistnieje") == []


def test_read_path_traversal_is_rejected(tmp_path):
    repo = FilesystemWorkspaceRepository(tmp_path)
    with pytest.raises(WriteError, match="poza katalogiem"):
        repo.read("teams_graph/abc", "../../../secret.env")


@pytest.mark.skipif(os.name != "nt", reason="separator '\\' i drive-relative to specyfika Windows")
def test_read_backslash_traversal_is_rejected_on_windows(tmp_path):
    """REGRESJA Windows: separator '\\' też jest ochroniony przez resolve().relative_to."""
    repo = FilesystemWorkspaceRepository(tmp_path)
    with pytest.raises(WriteError, match="poza katalogiem"):
        repo.read("teams_graph\\abc", "..\\..\\..\\secret.env")


def test_prune_stale_removes_idle_conversation_dirs(tmp_path):
    """TTL (ADR 0018): katalog rozmowy bez aktywności > retencji jest usuwany; świeży zostaje."""
    writer = FilesystemWorkspaceWriter(tmp_path)
    writer.create("teams_graph/stary/a.md", "x")
    writer.create("teams_graph/swiezy/b.md", "y")
    old_time = (datetime.now(tz=timezone.utc) - timedelta(days=40)).timestamp()
    os.utime(tmp_path / "teams_graph" / "stary" / "a.md", (old_time, old_time))

    removed = prune_stale(
        tmp_path, older_than=timedelta(days=30), now=datetime.now(tz=timezone.utc)
    )

    assert removed == 1
    assert not (tmp_path / "teams_graph" / "stary").exists()
    assert (tmp_path / "teams_graph" / "swiezy").exists()
