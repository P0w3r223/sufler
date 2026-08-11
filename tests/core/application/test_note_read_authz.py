"""Autoryzacja ODCZYTU bazy wiedzy (ADR 0062) — decyzja domenowa + serwis nad atrapą lookupu.

Bliźniak ``test_meeting_authz`` dla odczytu: rozpoznany członek → wolno, nieznany/pusty → odmowa
(fail-closed). Bez Graph i bez sieci — atrapa ``AadIdentityLookup`` lokalna w pliku.
"""

from __future__ import annotations

import pytest

from workmate.core.application.note_read_authz import NoteReadAuthorizer
from workmate.core.domain.authorization import Actor, can_read_note
from workmate.core.domain.identity import Person
from workmate.core.errors import NoteAuthorizationError


class _FakeLookup:
    """Atrapa ``AadIdentityLookup`` — zna wskazane AAD id, resztę zwraca ``None`` (fail-closed)."""

    def __init__(self, people: dict[str, Person]) -> None:
        self._people = people

    def resolve_by_aad_user_id(self, aad_user_id: str) -> Person | None:
        return self._people.get(aad_user_id)


_ANNA = Person(
    source_id="EMP-1",
    aad_user_id="aad-anna",
    jira_user="anna@example.org",
    display_name="Anna Kowalska",
)


def test_can_read_note_membership_only():
    # Bramka członkostwa: rozpoznany aktor → wolno; None → nie. Projekt nie różnicuje (szew).
    actor = Actor(aad_user_id="aad-anna", display_name="Anna Kowalska")
    assert can_read_note(actor) is True
    assert can_read_note(actor, project="scada-integration") is True
    assert can_read_note(None) is False


def test_known_member_returns_actor():
    authz = NoteReadAuthorizer(_FakeLookup({"aad-anna": _ANNA}))

    actor = authz.authorize("aad-anna")

    assert actor == Actor(aad_user_id="aad-anna", display_name="Anna Kowalska")


def test_unknown_sender_is_refused():
    authz = NoteReadAuthorizer(_FakeLookup({"aad-anna": _ANNA}))

    with pytest.raises(NoteAuthorizationError, match="nie jest rozpoznanym członkiem"):
        authz.authorize("aad-obcy")


def test_empty_sender_is_refused_without_resolve():
    # Pusty AAD id (drzwi/tura bez tożsamości nadawcy) → fail-closed, bez pytania lookupu.
    authz = NoteReadAuthorizer(_FakeLookup({"aad-anna": _ANNA}))

    with pytest.raises(NoteAuthorizationError, match="odczyt bazy wiedzy odrzucony"):
        authz.authorize("")
