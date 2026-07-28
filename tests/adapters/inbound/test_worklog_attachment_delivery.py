"""Testy wspólnej dostawy worklogu załącznikiem (A′4) — fail-fast bramki scope'ów.

Wspólny moduł ``attachment_delivery`` obsługuje OBA drzwi worklogów (wsadowe + self-service), więc
walidacja scope'ów jest testowana raz, w jednym miejscu. Samo wgranie (``send_worklog_document``) to
kompozycja I/O nad ``UserDocSender`` (testowanym osobno) — tu sprawdzamy tylko bramkę.
"""

from __future__ import annotations

import pytest

from workmate.adapters.inbound.worklogi.attachment_delivery import require_attachment_scopes
from workmate.config import TeamsPushSettings, WorklogiSettings


def _settings(*, enable_attachment: bool) -> WorklogiSettings:
    return WorklogiSettings(enable_attachment=enable_attachment)


def _push(scopes: tuple[str, ...]) -> TeamsPushSettings:
    return TeamsPushSettings(scopes=scopes)


def test_gate_off_accepts_any_scopes() -> None:
    """Bramka OFF — nie wymagamy zapisu do plików (dostawa idzie ścieżką tekstem)."""
    require_attachment_scopes(_settings(enable_attachment=False), _push(("Chat.Create",)))


def test_gate_on_without_files_scope_fails_fast() -> None:
    """Bramka ON bez zakresu zapisu = cicha, martwa konfiguracja → twardy błąd startu."""
    with pytest.raises(SystemExit, match="Files.ReadWrite"):
        require_attachment_scopes(_settings(enable_attachment=True), _push(("Chat.Create",)))


def test_gate_on_accepts_narrow_files_readwrite() -> None:
    """Least-privilege: węższy ``Files.ReadWrite`` (własny dysk) wystarcza."""
    require_attachment_scopes(
        _settings(enable_attachment=True), _push(("Chat.Create", "Files.ReadWrite"))
    )


def test_gate_on_accepts_broad_files_readwrite_all() -> None:
    """Szerszy ``Files.ReadWrite.All`` (już skonsentowany dla ADR 0026) też przechodzi."""
    require_attachment_scopes(
        _settings(enable_attachment=True), _push(("Chat.Create", "Files.ReadWrite.All"))
    )
