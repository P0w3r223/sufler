"""Testy wiringu drzwi Teams (Bot Framework): bramka ODCZYTU bazy wiedzy (ADR 0062).

Audyt 2026-08-17 wykazał, że te drzwi budowały responder BEZ ``note_read_authorizer``, więc
bramka odczytu nie istniała tu wcale — ``/szukaj``, ``/projekty``, ``/status`` i narzędzia
odczytu agenta jechały bez sprawdzenia nadawcy, podczas gdy bliźniacze drzwi delegowane
odmawiały. Te testy trzymają wpięcie: builder responder'a MUSI dostać authorizer, gdy bramka
jest włączona, a zbudowany authorizer musi być fail-closed.

Bez SDK i bez sieci: ``build_conversational_responder`` jest tu podmieniany atrapą (składanie
prawdziwego respondera oznaczałoby runtime agenta, Claude API i bazę rozmów).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workmate.adapters.inbound.teams import app as teams_app
from workmate.config import TeamsSettings
from workmate.core.errors import NoteAuthorizationError

_IDENTITIES = "EMP-042:\n  aad_user_id: aad-anna\n  jira_user: anna@example.org\n"


def _identities_file(tmp_path: Path) -> Path:
    path = tmp_path / "identities.yaml"
    path.write_text(_IDENTITIES, encoding="utf-8")
    return path


@pytest.fixture
def captured_wiring(monkeypatch):
    """Podmień wspólny builder respondera i zwróć słownik z przekazanymi argumentami."""
    seen: dict[str, object] = {}

    def _fake_builder(*args: object, **kwargs: object) -> object:
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(teams_app, "build_conversational_responder", _fake_builder)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "klucz-testowy")
    return seen


# --- budowa authorizera -----------------------------------------------------


def test_authorizer_is_absent_when_gate_is_off(tmp_path: Path):
    """Bramka OFF (domyślnie) → ``None``, czyli zachowanie sprzed ADR 0062."""
    settings = TeamsSettings(identities=_identities_file(tmp_path))

    assert teams_app.build_note_read_authorizer(settings) is None


def test_authorizer_refuses_unknown_and_anonymous_senders(tmp_path: Path):
    """NEGATYWNY: nierozpoznany nadawca nie dostaje odczytu bazy wiedzy przy włączonej bramce.

    Pusty ``sender_id`` to przypadek gościa i konta bez AAD — Bot Framework nie poda wtedy
    ``aadObjectId``, a drzwi nie podstawiają w to miejsca id kanałowego (``bot._sender_aad_id``).
    """
    settings = TeamsSettings(enable_note_read_authz=True, identities=_identities_file(tmp_path))
    authorizer = teams_app.build_note_read_authorizer(settings)
    assert authorizer is not None

    for sender_id in ("", "aad-obcy", "29:kanałowy-identyfikator-bot-framework"):
        with pytest.raises(NoteAuthorizationError):
            authorizer.authorize(sender_id)


def test_authorizer_admits_a_mapped_member(tmp_path: Path):
    settings = TeamsSettings(enable_note_read_authz=True, identities=_identities_file(tmp_path))
    authorizer = teams_app.build_note_read_authorizer(settings)
    assert authorizer is not None

    assert authorizer.authorize("aad-anna") is not None


# --- wpięcie w responder ----------------------------------------------------


def test_responder_gets_the_authorizer_when_gate_is_on(tmp_path: Path, captured_wiring):
    settings = TeamsSettings(enable_note_read_authz=True, identities=_identities_file(tmp_path))

    teams_app.build_responder(settings)

    assert captured_wiring["note_read_authorizer"] is not None


def test_responder_keeps_the_read_only_profile(tmp_path: Path, captured_wiring):
    """Profil drzwi (ADR 0006) nie zmienia się razem z bramką odczytu: zapis dalej wyłączony."""
    settings = TeamsSettings(enable_note_read_authz=True, identities=_identities_file(tmp_path))

    teams_app.build_responder(settings)

    assert captured_wiring["enable_write"] is False
    assert captured_wiring["channel"] == "teams"
    assert captured_wiring["safe"] is True


def test_responder_gets_no_authorizer_when_gate_is_off(captured_wiring):
    teams_app.build_responder(TeamsSettings())

    assert captured_wiring["note_read_authorizer"] is None
