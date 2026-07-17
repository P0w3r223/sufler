"""Testy walidacji ``WorkspaceSettings`` (ADR 0018): granica ``data/`` i deny-lista rozszerzeń.

Inwariant bezpieczeństwa: katalog roboczy (scratch od niezaufanego modelu) MUSI leżeć POZA bazą
wiedzy, a biała lista rozszerzeń NIE może zawierać plików wykonywalnych (nawet z env operatora).
"""

from __future__ import annotations

import pytest

from workmate.config import WorkspaceSettings


def test_validate_rejects_workspace_inside_data_dir(tmp_path):
    data = tmp_path / "data"
    settings = WorkspaceSettings(workspace_dir=data / "notes" / "ws")

    with pytest.raises(ValueError, match="wewnątrz katalogu danych"):
        settings.validate(data_dir=data)


def test_validate_rejects_workspace_equal_to_data_dir(tmp_path):
    data = tmp_path / "data"
    settings = WorkspaceSettings(workspace_dir=data)

    with pytest.raises(ValueError, match="wewnątrz katalogu danych"):
        settings.validate(data_dir=data)


def test_validate_rejects_executable_extension_even_from_operator(tmp_path):
    settings = WorkspaceSettings(workspace_dir=tmp_path / "ws", allowed_ext=("md", "exe"))

    with pytest.raises(ValueError, match="wykonywalne"):
        settings.validate(data_dir=tmp_path / "data")


def test_validate_accepts_workspace_outside_data_with_text_ext(tmp_path):
    settings = WorkspaceSettings(workspace_dir=tmp_path / "ws")  # domyślne md/txt/csv/json

    settings.validate(data_dir=tmp_path / "data")  # nie rzuca
