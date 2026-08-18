"""Testy walidacji ``WorkspaceSettings`` (ADR 0018): granica ``data/`` i deny-lista rozszerzeń.

Inwariant bezpieczeństwa: katalog roboczy (scratch od niezaufanego modelu) MUSI leżeć POZA bazą
wiedzy, a biała lista rozszerzeń NIE może zawierać plików wykonywalnych (nawet z env operatora).
"""

from __future__ import annotations

from pathlib import Path

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


# --- Korzeń brudnopisu przestał być wartością bezczynną (sprzątacz TTL bezwarunkowy) ----


def test_katalog_domowy_jako_brudnopis_wywala_start(tmp_path: Path):
    """Odkąd sprzątacz TTL biegnie przy KAŻDYM starcie drzwi, ta pomyłka nie jest bezobjawowa:
    kasuje wszystko na drugim poziomie katalogu domowego, czego nikt nie tknął od 30 dni."""
    ustawienia = WorkspaceSettings(enabled=True, workspace_dir=Path.home())

    with pytest.raises(ValueError, match="katalogiem domowym"):
        ustawienia.validate(data_dir=tmp_path / "data")


def test_korzen_systemu_jako_brudnopis_wywala_start(tmp_path: Path):
    ustawienia = WorkspaceSettings(enabled=True, workspace_dir=Path("/"))

    with pytest.raises(ValueError, match="korzeniem systemu"):
        ustawienia.validate(data_dir=tmp_path / "data")


def test_brudnopis_zawierajacy_baze_wiedzy_wywala_start(tmp_path: Path):
    """Odwrotność istniejącego inwariantu ADR 0018: dotąd pilnowaliśmy, żeby brudnopis nie leżał
    W BAZIE WIEDZY. Sprzątacz dokłada drugi kierunek — brudnopis NAD bazą wiedzy sięgnąłby jej
    kasowaniem."""
    ustawienia = WorkspaceSettings(enabled=True, workspace_dir=tmp_path)

    with pytest.raises(ValueError, match="zawiera katalog danych"):
        ustawienia.validate(data_dir=tmp_path / "data")


def test_zwykly_brudnopis_obok_bazy_wiedzy_przechodzi(tmp_path: Path):
    """Kontrast: układ produkcyjny (osobne wolumeny) MUSI przejść — inaczej bramka byłaby martwa."""
    WorkspaceSettings(enabled=True, workspace_dir=tmp_path / "scratchpad").validate(
        data_dir=tmp_path / "data"
    )
