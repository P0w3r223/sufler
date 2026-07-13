"""Testy czystej domeny katalogu roboczego (ADR 0018): bezpieczne nazwy i izolacja scope.

Sedno bezpieczeństwa: nazwa pliku od modelu przechodzi przez slug + białą listę rozszerzeń
(``/``, ``..``, ścieżki absolutne nie przeżyją), a różne rozmowy trafiają do RÓŻNYCH katalogów.
"""
from __future__ import annotations

import pytest

from workmate.core.domain.workspace import (
    WorkspaceScope,
    relpath_in_scope,
    safe_filename,
)
from workmate.core.errors import WriteError

_ALLOWED = frozenset({"md", "txt", "csv", "json"})


def test_safe_filename_slugifies_stem_and_lowercases_ext():
    assert safe_filename("Raport MPWiK.MD", allowed_ext=_ALLOWED) == "raport-mpwik.md"


def test_safe_filename_rejects_disallowed_extension():
    with pytest.raises(WriteError):
        safe_filename("skrypt.exe", allowed_ext=_ALLOWED)


def test_safe_filename_rejects_missing_extension():
    with pytest.raises(WriteError):
        safe_filename("raport", allowed_ext=_ALLOWED)


def test_safe_filename_rejects_stem_without_sluggable_chars():
    with pytest.raises(WriteError):
        safe_filename("!!!.md", allowed_ext=_ALLOWED)


def test_safe_filename_neutralizes_path_traversal_in_name():
    # Nawet gdyby model wstawił separatory/„..", slug je usuwa → jeden bezpieczny segment.
    result = safe_filename("../../etc/passwd.txt", allowed_ext=_ALLOWED)
    assert "/" not in result and ".." not in result
    assert result.endswith(".txt")


def test_scope_isolates_different_conversations():
    a = WorkspaceScope("teams_graph", "team/chan/root-1")
    b = WorkspaceScope("teams_graph", "team/chan/root-2")

    assert a.dirpath() != b.dirpath()  # różne rozmowy → różne katalogi (izolacja)


def test_scope_is_deterministic_for_same_conversation():
    a = WorkspaceScope("teams_graph", "team/chan/root")
    b = WorkspaceScope("teams_graph", "team/chan/root")

    assert a.dirpath() == b.dirpath()  # ta sama rozmowa → ten sam katalog (kolejne tury)


def test_scope_dir_is_filesystem_safe_no_colon_or_at():
    # id rozmowy Teams niesie ':' i '@' — heszowanie daje FS-bezpieczny katalog.
    scope = WorkspaceScope("teams_graph", "27fe/19:xxx@thread.tacv2/1783")
    text = str(scope.dirpath())
    assert ":" not in text and "@" not in text
    assert text.startswith("teams-graph/")  # kanał sslugowany


def test_relpath_in_scope_composes_dir_and_filename():
    scope = WorkspaceScope("teams_graph", "team/chan/root")
    relpath = relpath_in_scope(scope, "raport.md")
    assert relpath == f"{scope.dirpath()}/raport.md"
