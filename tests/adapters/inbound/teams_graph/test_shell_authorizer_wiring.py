"""Strażnik wiringu POWŁOKI na drzwiach Teams (ADR 0063): bramka członkostwa + fail-fast.

Powłoka (``Bash``, ADR 0057) sięga ścieżką bezwzględną poza scope rozmowy, więc na drzwiach
WIELOUŻYTKOWNIKOWYCH musi jechać na tej samej granicy zaufania co dane (``identities.yaml``), a nie
na luźniejszym udziale w kanale. Te testy pilnują, że drzwi Teams nigdy nie zbudują powłoki bez
autoryzatora, gdy jest włączona: OFF → ``None`` (nie ma czego bramkować), ON → ``ShellAuthorizer``,
ON bez mapy tożsamości → fail-fast (inaczej KAŻDY nadawca dostałby powłokę — luka, którą ADR 0063
zamyka).
"""

from __future__ import annotations

import pytest

from workmate.adapters.inbound.teams_graph.app import _build_shell_authorizer
from workmate.config import ShellSettings, TeamsGraphSettings


def test_wiring_returns_none_when_shell_off(tmp_path):
    # Powłoka wyłączona → nie ma narzędzia, nie ma czego bramkować.
    settings = TeamsGraphSettings(client_id="a", tenant_id="t")
    shell = ShellSettings(enabled=False, manager_socket_path=tmp_path / "control.sock")

    assert _build_shell_authorizer(settings, shell) is None


def test_wiring_builds_authorizer_when_shell_on(tmp_path):
    identities = tmp_path / "identities.yaml"
    identities.write_text("", encoding="utf-8")
    settings = TeamsGraphSettings(client_id="a", tenant_id="t", meeting_note_identities=identities)
    shell = ShellSettings(enabled=True, manager_socket_path=tmp_path / "control.sock")

    # Powłoka ON → autoryzator istnieje (bramki członkostwa nie może zabraknąć w wiringu).
    assert _build_shell_authorizer(settings, shell) is not None


def test_wiring_failfast_when_shell_on_without_identities(tmp_path):
    # Powłoka ON bez mapy tożsamości = każdy nadawca dostałby powłokę (luka ADR 0063). Fail-fast,
    # jak przy zapisie (ADR 0042) i odczycie (ADR 0062).
    settings = TeamsGraphSettings(client_id="a", tenant_id="t")  # meeting_note_identities = brak
    shell = ShellSettings(enabled=True, manager_socket_path=tmp_path / "control.sock")

    with pytest.raises(SystemExit, match="WORKMATE_ENABLE_SHELL"):
        _build_shell_authorizer(settings, shell)
