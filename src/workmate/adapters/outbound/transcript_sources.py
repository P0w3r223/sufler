"""Adaptery źródła transkryptu (port ``TranscriptSource``, Faza 2 M3 / ADR 0009).

``GraphTranscriptSource`` — realne pobranie z Microsoft Graph — jest ODŁOŻONE do
czasu dostępu do Azure/M365 (rejestracja aplikacji w Entra ID + uprawnienia
``OnlineMeetingTranscript.Read.All`` + tożsamość). Bez tego nie da się go ani
zaimplementować, ani zweryfikować — stąd świadomy stub, który jasno mówi, czego
brakuje, zamiast udawać działanie.

``InMemoryTranscriptSource`` pozwala uruchomić i zademonstrować cały przepływ M3
lokalnie (wklejony transkrypt), bez Azure — do testów i lokalnego harnessu.
"""
from __future__ import annotations


class GraphTranscriptSource:
    """Stub źródła transkryptu z Microsoft Graph — do wpięcia po uzyskaniu Azure."""

    def fetch(self, meeting_ref: str) -> str:
        raise NotImplementedError(
            "Pobieranie transkryptu z Microsoft Graph wymaga dostępu do Azure/M365 "
            "(rejestracja aplikacji w Entra ID + uprawnienia Graph). Odłożone do czasu "
            "dostępu — patrz docs/adr/0009-meeting-note-flow-and-write-surface.md (M3). "
            "Do testu/lokalnie użyj InMemoryTranscriptSource."
        )


class InMemoryTranscriptSource:
    """Transkrypty w pamięci (lokalny harness/test) — mapowanie ``meeting_ref`` → treść."""

    def __init__(self, transcripts: dict[str, str]) -> None:
        self._transcripts = transcripts

    def fetch(self, meeting_ref: str) -> str:
        try:
            return self._transcripts[meeting_ref]
        except KeyError as exc:
            raise KeyError(f"brak transkryptu dla spotkania: {meeting_ref!r}") from exc
