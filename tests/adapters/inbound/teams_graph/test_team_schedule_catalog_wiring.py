"""Testy wiringu ``_build_team_schedule_catalog`` (ADR 0056) — bramka ``auto`` na cudzym cache MSAL.

Sedno: narzędzie grafiku wchodzi TYLKO gdy ``ScheduleSettings.is_enabled()`` jest prawdziwe (tryb
``auto`` sam sprawdza obecność zamontowanego cache tokenu bota powiadomienia-teams) — na hoście
bez tego montu drzwi po prostu nie wystawiają ``get_team_schedule`` (ciche wyłączenie, nie błąd).
Cichy token (MSAL) jest LENIWY — ta warstwa nie woła go, więc test nie potrzebuje realnego ``msal``.
"""

from __future__ import annotations

from pathlib import Path

from workmate.adapters.inbound.teams_graph.app import _build_team_schedule_catalog
from workmate.config import ScheduleSettings


def test_disabled_by_default_without_cache_file(tmp_path: Path) -> None:
    settings = ScheduleSettings(
        client_id="x", tenant_id="y", token_cache_path=tmp_path / "brak-cache.bin"
    )
    assert _build_team_schedule_catalog(settings) == []


def test_disabled_when_explicitly_off(tmp_path: Path) -> None:
    cache = tmp_path / "cache.bin"
    cache.write_text("{}", encoding="utf-8")
    settings = ScheduleSettings(
        client_id="x", tenant_id="y", token_cache_path=cache, enabled="false"
    )
    assert _build_team_schedule_catalog(settings) == []


def test_enabled_auto_when_app_and_cache_present(tmp_path: Path) -> None:
    cache = tmp_path / "cache.bin"
    cache.write_text("{}", encoding="utf-8")
    settings = ScheduleSettings(client_id="x", tenant_id="y", token_cache_path=cache)
    tools = _build_team_schedule_catalog(settings)
    assert {t.name for t in tools} == {"get_team_schedule"}


def test_forced_enabled_true_without_app_configured_still_builds() -> None:
    """``enabled=true`` force — nie sprawdzamy client_id/tenant_id tutaj (fail przy realnym
    wywołaniu)."""
    settings = ScheduleSettings(enabled="true")
    tools = _build_team_schedule_catalog(settings)
    assert {t.name for t in tools} == {"get_team_schedule"}
