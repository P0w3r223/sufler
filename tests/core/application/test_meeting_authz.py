"""Testy autoryzacji nadawcy ``/notatka`` (B2 / ADR 0042) — port tożsamości + czysta decyzja.

Cała logika stoi na ATRAPIE portu ``AadIdentityLookup`` — bez Graph, bez YAML, bez sieci. Klucz:
rozpoznany członek → ``Actor``; nieznany / pusty AAD id → ``NoteAuthorizationError`` (fail-closed),
podniesiony ZANIM cokolwiek ruszy (autoryzacja jest synchroniczna, przed transkryptem).
"""

from __future__ import annotations

import pytest

from workmate.core.application.meeting_authz import MeetingNoteAuthorizer
from workmate.core.domain.authorization import Actor, can_write_meeting_note
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


def test_known_member_returns_actor():
    authz = MeetingNoteAuthorizer(_FakeLookup({"aad-anna": _ANNA}))

    actor = authz.authorize("aad-anna", project="scada-integration")

    assert actor == Actor(aad_user_id="aad-anna", display_name="Anna Kowalska")


def test_unknown_sender_is_refused():
    authz = MeetingNoteAuthorizer(_FakeLookup({"aad-anna": _ANNA}))

    with pytest.raises(NoteAuthorizationError, match="nie jest rozpoznanym członkiem"):
        authz.authorize("aad-obcy", project="scada-integration")


def test_empty_sender_id_is_refused():
    # Drzwi bez tożsamości nadawcy → żaden zapis bez ustalonego autora (nie próbujemy resolve).
    authz = MeetingNoteAuthorizer(_FakeLookup({"aad-anna": _ANNA}))

    with pytest.raises(NoteAuthorizationError):
        authz.authorize("", project="scada-integration")


def test_can_write_meeting_note_membership_only():
    # B2-A: rozpoznany aktor → wolno; None → nie. Projekt nie różnicuje (szew pod B2-B).
    actor = Actor(aad_user_id="aad-anna", display_name="Anna Kowalska")
    assert can_write_meeting_note(actor, project="scada-integration") is True
    assert can_write_meeting_note(actor, project="inny-projekt") is True
    assert can_write_meeting_note(None, project="scada-integration") is False


# --- ADR 0070 §3: wpis „tylko Teams" to PEŁNE członkostwo -------------------------

_TADEUSZ = Person(
    source_id="EMP-51",
    aad_user_id="aad-tadek",
    jira_user="",  # osoba bez konta Jira — stan trwały i legalny (ADR 0070 §1)
    display_name="Tadeusz Anonimowski",
)


def test_member_without_jira_account_may_write_a_meeting_note():
    """Dwoje drzwi zapisu (``/notatka`` i przechwycenie „zapisz to") pyta tego samego autoryzatora.

    ADR 0070 §3 wylicza zapis notatki wśród zdolności, które wpis w mapie NADAJE — bez tej bramki
    zdanie z ADR-u nie ma nic, co by je trzymało.
    """
    authz = MeetingNoteAuthorizer(_FakeLookup({"aad-tadek": _TADEUSZ}))

    actor = authz.authorize("aad-tadek", project="scada-integration")

    assert actor == Actor(aad_user_id="aad-tadek", display_name="Tadeusz Anonimowski")
