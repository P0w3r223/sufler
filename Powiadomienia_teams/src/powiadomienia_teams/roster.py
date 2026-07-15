"""Wczytanie rosteru członków z pliku YAML.

Obejście braku uprawnienia `TeamMember.Read.All` — dopóki nie zostanie nadane, listę osób
podajemy ręcznie. Po nadaniu uprawnienia ten sam typ `Member` będzie budowany z odpowiedzi
Graph (`/teams/{id}/members`), a ten loader stanie się opcjonalny.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from powiadomienia_teams.domain.models import Member


def load_roster(path: Path) -> tuple[Member, ...]:
    """Wczytaj listę członków z YAML.

    Format: lista wpisów `{user_id, display_name, email?, roles?}`. Rzuca `ValueError`,
    gdy plik nie jest listą albo wpisowi brakuje pól wymaganych.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        raise ValueError(f"Roster {path} musi być listą wpisów, jest {type(raw).__name__}")
    members: list[Member] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict) or "user_id" not in entry or "display_name" not in entry:
            raise ValueError(f"Wpis rosteru #{i} wymaga pól 'user_id' i 'display_name': {entry!r}")
        email = entry.get("email")
        raw_roles = entry.get("roles")
        if raw_roles is None:
            roles: tuple[str, ...] = ()
        elif isinstance(raw_roles, list):
            roles = tuple(str(r) for r in raw_roles)
        else:
            raise ValueError(f"Wpis rosteru #{i}: 'roles' musi być listą: {raw_roles!r}")
        members.append(
            Member(
                user_id=str(entry["user_id"]),
                display_name=str(entry["display_name"]),
                email=(str(email) if email else None),
                roles=roles,
            )
        )
    return tuple(members)
