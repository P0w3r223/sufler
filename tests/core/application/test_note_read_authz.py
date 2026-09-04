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


# --- Klasa pochodzenia tury (ADR 0066) -----------------------------------------


def test_trust_class_is_t1_for_a_mapped_sender():
    """Ta sama rozdzielczość tożsamości zasila OBIE osie — zdolności i pochodzenie treści.

    Gdyby klasę liczył ktoś inny, odpowiedzi mogłyby się rozjechać: bot odmawiałby komuś
    zdolności, a jego słowa dalej traktował jak instrukcje.
    """
    authorizer = NoteReadAuthorizer(_FakeLookup({"aad-1": _ANNA}))

    assert authorizer.trust_class("aad-1") == "T1"


def test_trust_class_is_t2_for_an_unmapped_sender():
    authorizer = NoteReadAuthorizer(_FakeLookup({}))

    assert authorizer.trust_class("aad-obcy") == "T2"


def test_empty_sender_id_is_t2():
    """Pusty ``sender_id`` (bot, zdarzenie systemowe) to nie jest osoba — jego tekst to dane."""
    authorizer = NoteReadAuthorizer(_FakeLookup({"aad-1": _ANNA}))

    assert authorizer.trust_class("") == "T2"


def test_labelling_returns_a_class_instead_of_refusing():
    """Etykietowanie nie jest bramką: gość dostaje KLASĘ, a nie wyjątek — odmowy zostają
    w ``authorize``, żeby jedna zmiana nie zabrała botowi możliwości odpowiadania gościom."""
    authorizer = NoteReadAuthorizer(_FakeLookup({}))

    assert authorizer.trust_class("ktokolwiek") == "T2"
    with pytest.raises(NoteAuthorizationError):
        authorizer.authorize("ktokolwiek")  # ta sama osoba, druga oś: zdolność ODMÓWIONA


# --- ADR 0070 §3: wpis „tylko Teams" to PEŁNE członkostwo -------------------------

_TADEUSZ = Person(
    source_id="EMP-51",
    aad_user_id="aad-tadek",
    jira_user="",  # osoba bez konta Jira — stan trwały i legalny (ADR 0070 §1)
    display_name="Tadeusz Anonimowski",
)


def test_member_without_jira_account_may_read_notes():
    """Brak konta Jira NIE odbiera członkostwa — to jest cała teza ADR 0070 §3.

    Ta bramka istnieje, bo naprawa idzie w kierunku, w którym łatwo przesadzić: ktoś czytający
    „pusty ``jira_user``" jako „niekompletna tożsamość" dopisze warunek do autoryzatora i odetnie
    człowieka od bazy wiedzy — czyli odtworzy dokładnie ten stan, dla którego usunięcia ADR
    powstał. Autoryzacja rozstrzyga po ``aad_user_id`` i po niczym więcej.
    """
    authz = NoteReadAuthorizer(_FakeLookup({"aad-tadek": _TADEUSZ}))

    assert authz.authorize("aad-tadek") == Actor(
        aad_user_id="aad-tadek", display_name="Tadeusz Anonimowski"
    )


def test_member_without_jira_account_is_trust_class_t1():
    """Rozszczepienie T1/T2 (ADR 0066) jedzie za tym samym rozwiązaniem tożsamości.

    Gdyby brak konta Jira spychał do ``T2``, treść od pełnoprawnego członka pionu byłaby
    etykietowana jak treść z zewnątrz — i to bez śladu w odmowie, bo ``trust_class`` niczego
    nie odmawia, tylko opisuje pochodzenie.
    """
    authz = NoteReadAuthorizer(_FakeLookup({"aad-tadek": _TADEUSZ}))

    assert authz.trust_class("aad-tadek") == "T1"
