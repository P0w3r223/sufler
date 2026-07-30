"""Testy modelu ``Person`` (ADR 0042/0054) — most tożsamości Teams (AAD) ↔ Jira."""

from __future__ import annotations

from workmate.core.domain.identity import Person


def test_person_requires_only_identity_fields() -> None:
    person = Person(source_id="EMP-1", aad_user_id="aad-1", jira_user="a@example.com")
    assert person.display_name == ""  # opcjonalne, domyślnie puste


def test_person_carries_display_name_when_given() -> None:
    person = Person(
        source_id="EMP-1", aad_user_id="aad-1", jira_user="a@example.com", display_name="Anna"
    )
    assert person.display_name == "Anna"
