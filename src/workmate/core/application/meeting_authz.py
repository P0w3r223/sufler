"""Autoryzacja nadawcy komendy ``/notatka`` (B2 / ADR 0042) — port tożsamości + decyzja rdzenia.

Cienki serwis aplikacyjny: rozwiązuje ``aad_user_id`` nadawcy na osobę przez port
``AadIdentityLookup`` (implementowany przez katalog tożsamości worklogów — fail-closed), mapuje
na ``Actor`` i pyta czystą politykę ``can_write_meeting_note``. Odmowa → ``NoteAuthorizationError``.

Reguła zależności stoi: serwis woła PORT (adapter dostarcza rozwiązanie), a decyzja to czysta
funkcja domeny — całość testowalna na atrapie lookupu, bez Graph i bez sieci. Autoryzacja jest
SYNCHRONICZNA i biegnie PRZED wolnym łańcuchem (transkrypt + Claude), więc odmowa jest
natychmiastowa (ADR 0042/0043).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from workmate.core.domain.authorization import Actor, actor_from_person, can_write_meeting_note
from workmate.core.errors import NoteAuthorizationError

if TYPE_CHECKING:
    from workmate.core.ports.identity import AadIdentityLookup


class MeetingNoteAuthorizer:
    """Bramka członkostwa (B2-A): nadawca musi rozwiązać się do rozpoznanego członka pionu."""

    def __init__(self, identities: AadIdentityLookup) -> None:
        self._identities = identities

    def authorize(self, requester_aad_id: str, *, project: str) -> Actor:
        """Zwróć ``Actor`` uprawnionego nadawcy albo podnieś ``NoteAuthorizationError``.

        Fail-closed. Pusty ``requester_aad_id`` (drzwi bez tożsamości nadawcy) traktujemy jak
        nierozpoznanego — żaden zapis bez autora. ``project`` idzie do polityki (szew B2-B).
        """
        person = (
            self._identities.resolve_by_aad_user_id(requester_aad_id) if requester_aad_id else None
        )
        actor = actor_from_person(person) if person is not None else None
        if actor is None or not can_write_meeting_note(actor, project=project):
            raise NoteAuthorizationError(
                "nadawca nie jest rozpoznanym członkiem pionu — zapis notatki odrzucony "
                "(fail-closed, ADR 0042)"
            )
        return actor
