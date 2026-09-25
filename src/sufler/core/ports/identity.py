"""Port odwrotnego lookupu tożsamości: konto Teams (AAD) → osoba.

Wąski kontrakt współdzielony przez autoryzację notatki ze spotkania (ADR 0042) i "moje zadania"
Jira (ADR 0054) — obie ścieżki dostają ``aad_user_id`` nadawcy i potrzebują rozwiązanej ``Person``.
Implementacja (``YamlIdentityDirectory``) jest fail-closed: nieznane id → ``None``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sufler.core.domain.identity import Person


class AadIdentityLookup(Protocol):
    """Odwrotny lookup: konto Teams (``aad_user_id``) → osoba, albo ``None`` (fail-closed)."""

    def resolve_by_aad_user_id(self, aad_user_id: str) -> Person | None: ...
