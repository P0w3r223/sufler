"""Autoryzacja narzędzia POWŁOKI (ADR 0063) — port tożsamości + decyzja rdzenia.

Bliźniak ``note_read_authz.NoteReadAuthorizer``, ale dla powłoki (``Bash``, ADR 0057) i z jedną
różnicą kształtu: odmowa to OMINIĘCIE narzędzia (build-time), nie odmowa w czasie wywołania. Dlatego
``resolve`` zwraca ``Actor | None`` zamiast podnosić błąd — fabryka powłoki po prostu nie dokłada
narzędzia nierozpoznanemu nadawcy (jak Jira ADR 0054, nie jak odczyt ADR 0062, gdzie narzędzie
zostaje i odmawia). Gość nie musi się dowiadywać, że powłoka istnieje.

Rozwiązuje ``aad_user_id`` nadawcy na osobę przez port ``AadIdentityLookup`` (fail-closed), mapuje
na ``Actor`` i pyta czystą politykę ``can_use_shell``. Serwis woła PORT, decyzja to czysta funkcja
domeny — całość testowalna na atrapie lookupu, bez Graph i bez sieci.

Egzekwowany na drzwiach WIELOUŻYTKOWNIKOWYCH (Teams), gdzie tożsamość nadawcy jest znana. Drzwi MCP
i CLI (jeden zaufany operator, brak ``sender_id``) są POZA zakresem — jak w ADR 0042/0062 — więc tam
bramka nie jest wpinana (``build_conversational_responder`` z ``shell_authorizer=None``). Cross-read
MIĘDZY rozpoznanymi członkami ścieżką bezwzględną zostaje — domyka go montaż per-rozmowa (ADR 0063
§2 / infra ADR 0010), nie ta bramka.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from workmate.core.domain.authorization import Actor, actor_from_person, can_use_shell

if TYPE_CHECKING:
    from workmate.core.ports.identity import AadIdentityLookup


class ShellAuthorizer:
    """Bramka członkostwa POWŁOKI (ADR 0063): nadawca musi rozwiązać się do członka pionu."""

    def __init__(self, identities: AadIdentityLookup) -> None:
        self._identities = identities

    def resolve(self, requester_aad_id: str) -> Actor | None:
        """Zwróć ``Actor`` uprawnionego członka albo ``None`` (fail-closed → powłoka pominięta).

        Pusty ``requester_aad_id`` (drzwi/tura bez tożsamości nadawcy) traktujemy jak
        nierozpoznanego — żadnej powłoki bez rozpoznanego członka. Zwraca ``None`` zamiast podnosić
        błąd, bo odmowa tu to ominięcie narzędzia (build-time, ADR 0063 §3), nie komunikat.
        """
        person = (
            self._identities.resolve_by_aad_user_id(requester_aad_id) if requester_aad_id else None
        )
        actor = actor_from_person(person) if person is not None else None
        return actor if actor is not None and can_use_shell(actor) else None
