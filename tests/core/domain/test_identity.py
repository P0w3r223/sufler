"""Testy modelu ``Person`` (ADR 0042/0054) — most tożsamości Teams (AAD) ↔ Jira."""

from __future__ import annotations

from workmate.core.domain.identity import Person


def test_person_display_name_is_optional() -> None:
    person = Person(source_id="EMP-1", aad_user_id="aad-1", jira_user="a@example.com")
    assert person.display_name == ""  # opcjonalne, domyślnie puste


def test_person_without_jira_user_is_a_teams_only_member() -> None:
    """Brak konta Jira jest stanem MODELU, nie brakiem danych (ADR 0070 §1).

    Domyślna wartość musi siedzieć w modelu, a nie tylko w ładowarce: mapa nie jest jedyną drogą
    powstania ``Person`` — testy i wiring budują go wprost, a bez domyślnej każdy taki wołający
    musiałby podać puste pole ręcznie, czyli zaprosić z powrotem regułę, którą 0070 zdejmuje.
    """
    person = Person(source_id="EMP-1", aad_user_id="aad-1")
    assert person.jira_user == ""


def test_person_carries_display_name_when_given() -> None:
    person = Person(
        source_id="EMP-1", aad_user_id="aad-1", jira_user="a@example.com", display_name="Anna"
    )
    assert person.display_name == "Anna"
