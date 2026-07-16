"""Port mapowania wątku Teams ↔ cel GitHub (issue/PR) — warstwa spajająca Fazy 3 (ADR 0024).

``Protocol`` jak pozostałe porty. Trwałe, dwukierunkowe powiązanie: „wątek (root) na kanale ↔
numer issue/PR", żeby (a) notifier dopisywał kolejne zdarzenia tego samego issue/PR do TEGO SAMEGO
wątku (zamiast tworzyć nowy root), a (b) reaktywne drzwi wiedziały, którego issue dotyczy odpowiedź
w wątku. Domyślny adapter: SQLite nad TYM SAMYM plikiem ``events.db`` co ``EventStore``, ale w
OSOBNEJ tabeli (EventStore pozostaje nietknięty). Klucz jednoznaczności: ``(team, channel, kind,
number)`` — jeden wątek na cel; ``link`` jest upsertem (nadpisuje root), żeby po usunięciu roota
w Teams notifier mógł przełączyć wątek zamiast blokować strumień.
"""
from __future__ import annotations

from typing import Protocol


class ThreadLinkStore(Protocol):
    """Powiązanie wątek↔cel: root po celu, cel po roocie, utworzenie linku (dwukierunkowe)."""

    def get_root(
        self, team_id: str, channel_id: str, target_kind: str, target_number: str
    ) -> str | None:
        """Id roota wątku dla celu ``(kind, number)``; ``None`` gdy brak (root do utworzenia)."""
        ...

    def get_target(
        self, team_id: str, channel_id: str, root_id: str
    ) -> tuple[str, str] | None:
        """Cel ``(kind, number)`` dla roota wątku; ``None`` gdy wątek nie wiąże się z issue/PR."""
        ...

    def link(
        self,
        team_id: str,
        channel_id: str,
        target_kind: str,
        target_number: str,
        root_id: str,
    ) -> None:
        """Zapisz/NADPISZ powiązanie celu z rootem (upsert — przełącza wątek po usunięciu roota)."""
        ...
