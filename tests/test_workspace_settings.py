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


# --- Trzeci kierunek: brudnopis nad wolumenem stanu (migawki notatek, ADR 0065) --------------


def _stan(tmp_path: Path) -> tuple[Path, tuple[tuple[Path, str, bool], ...]]:
    """Układ floty: brudnopis i migawki notatek jako RODZEŃSTWO na wolumenie stanu."""
    wolumen = tmp_path / "var-lib-workmate"
    return wolumen, ((wolumen / "snapshots" / "notes", "WORKMATE_NOTE_SNAPSHOTS_DIR", True),)


def test_brudnopis_na_korzeniu_wolumenu_stanu_wywala_start(tmp_path: Path):
    """Zgubiony segment `/workspace` przechodził obie dotychczasowe kontrole, a sprzątacz TTL
    trafiał wtedy dokładnie w `snapshots/notes` — czyli w migawki sprzed mutacji notatek."""
    wolumen, trwale = _stan(tmp_path)
    ustawienia = WorkspaceSettings(enabled=True, workspace_dir=wolumen)

    with pytest.raises(ValueError, match="WORKMATE_NOTE_SNAPSHOTS_DIR"):
        ustawienia.validate(data_dir=tmp_path / "data", persistent_paths=trwale)


def test_brudnopis_rowny_katalogowi_migawek_wywala_start(tmp_path: Path):
    wolumen, trwale = _stan(tmp_path)
    ustawienia = WorkspaceSettings(enabled=True, workspace_dir=wolumen / "snapshots" / "notes")

    with pytest.raises(ValueError, match="WORKMATE_NOTE_SNAPSHOTS_DIR"):
        ustawienia.validate(data_dir=tmp_path / "data", persistent_paths=trwale)


def test_brudnopis_obok_migawek_przechodzi(tmp_path: Path):
    """Kontrast: dokładnie układ floty (`workspace` obok `snapshots/notes`) MUSI przejść."""
    wolumen, trwale = _stan(tmp_path)

    WorkspaceSettings(enabled=True, workspace_dir=wolumen / "workspace").validate(
        data_dir=tmp_path / "data", persistent_paths=trwale
    )


"""Szew „drzwi podają tę listę" pilnuje
``tests/adapters/inbound/teams_graph/test_startup_writable.py`` — tam stoi już harness startu."""
