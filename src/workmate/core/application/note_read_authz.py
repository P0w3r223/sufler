"""Autoryzacja ODCZYTU bazy wiedzy (ADR 0062) — port tożsamości + decyzja rdzenia.

Bliźniak ``meeting_authz.MeetingNoteAuthorizer``, ale dla odczytu. Rozwiązuje ``aad_user_id``
nadawcy na osobę przez port ``AadIdentityLookup`` (fail-closed), mapuje na ``Actor`` i pyta czystą
politykę ``can_read_note``. Odmowa → ``NoteAuthorizationError`` (ten sam błąd rdzenia co zapis).

Reguła zależności stoi: serwis woła PORT (adapter dostarcza rozwiązanie), decyzja to czysta funkcja
domeny — całość testowalna na atrapie lookupu, bez Graph i bez sieci. Egzekwowany na TYPOWANYCH
ścieżkach odczytu (narzędzia agenta ``search_notes``/``get_note``/``list_projects`` wiązane per turę
z nadawcą, oraz komendy ``/szukaj``/``/projekty``). Ścieżka powłoki (``cat``/``workmate-search`` po
montażu ``ro``) jest POZA zakresem: nie niesie tożsamości nadawcy, a domknięcie wymaga montażu
per-rozmowa (infra ADR 0010) — patrz ADR 0062 §Decision 4.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from workmate.core.domain.authorization import Actor, actor_from_person, can_read_note
from workmate.core.errors import NoteAuthorizationError

if TYPE_CHECKING:
    from workmate.core.ports.identity import AadIdentityLookup


class NoteReadAuthorizer:
    """Bramka członkostwa odczytu (ADR 0062): nadawca musi rozwiązać się do członka pionu."""

    def __init__(self, identities: AadIdentityLookup) -> None:
        self._identities = identities

    def authorize(self, requester_aad_id: str) -> Actor:
        """Zwróć ``Actor`` uprawnionego czytelnika albo podnieś ``NoteAuthorizationError``.

        Fail-closed. Pusty ``requester_aad_id`` (drzwi/tura bez tożsamości nadawcy) traktujemy jak
        nierozpoznanego — żaden odczyt bazy bez rozpoznanego członka.
        """
        person = (
            self._identities.resolve_by_aad_user_id(requester_aad_id) if requester_aad_id else None
        )
        actor = actor_from_person(person) if person is not None else None
        if actor is None or not can_read_note(actor):
            raise NoteAuthorizationError(
                "nadawca nie jest rozpoznanym członkiem pionu — odczyt bazy wiedzy odrzucony "
                "(fail-closed, ADR 0062)"
            )
        return actor
