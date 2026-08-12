"""Autoryzacja narzędzia POWŁOKI (ADR 0063) — decyzja domenowa + serwis nad atrapą lookupu.

Bliźniak ``test_note_read_authz`` dla powłoki, z jedną różnicą kształtu: odmowa to ``None`` (powłoka
POMINIĘTA, build-time), nie wyjątek. Rozpoznany członek → ``Actor``; nieznany/pusty → ``None``
(fail-closed). Bez Graph i bez sieci — atrapa ``AadIdentityLookup`` lokalna w pliku.
"""

from __future__ import annotations

from workmate.core.application.shell_authz import ShellAuthorizer
from workmate.core.domain.authorization import Actor, can_use_shell
from workmate.core.domain.identity import Person


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


def test_can_use_shell_membership_only():
    # Bramka członkostwa: rozpoznany aktor → wolno; None → nie.
    actor = Actor(aad_user_id="aad-anna", display_name="Anna Kowalska")
    assert can_use_shell(actor) is True
    assert can_use_shell(None) is False


def test_known_member_resolves_actor():
    authz = ShellAuthorizer(_FakeLookup({"aad-anna": _ANNA}))

    actor = authz.resolve("aad-anna")

    assert actor == Actor(aad_user_id="aad-anna", display_name="Anna Kowalska")


def test_unknown_sender_resolves_none():
    # Nieznany nadawca → None (powłoka pominięta), NIE wyjątek — inaczej niż odczyt notatek.
    authz = ShellAuthorizer(_FakeLookup({"aad-anna": _ANNA}))

    assert authz.resolve("aad-obcy") is None


def test_empty_sender_resolves_none_without_resolve():
    # Pusty AAD id (drzwi/tura bez tożsamości nadawcy) → None, bez pytania lookupu (fail-closed).
    authz = ShellAuthorizer(_FakeLookup({"aad-anna": _ANNA}))

    assert authz.resolve("") is None
