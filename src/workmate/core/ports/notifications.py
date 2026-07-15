"""Port proaktywnego powiadamiania Teams (dual-target, ADR 0022) — kierunek WYJŚCIOWY.

``Protocol`` jak pozostałe porty. Notifier rdzenia (``EventNotifier``) zależy tylko od tego
kontraktu, więc pełną logikę testujemy atrapą — bez ``httpx``/MSAL. Adapter (``HttpxTeamsNotifier``)
tłumaczy ``text`` (Markdown) na HTML Teams i wypycha go do celu; rdzeń o HTML/Graph nie wie.

Dwa cele, oba konfigurowalne (decyzja użytkownika): czat 1:1 do osoby oraz post na kanał zespołu.
"""
from __future__ import annotations

from typing import Protocol


class TeamsNotifier(Protocol):
    """Wypchnięcie wiadomości do Teams: czat 1:1 albo nowy post na kanale (oba async)."""

    async def send_chat(self, target_user_id: str, text: str) -> None:
        """Wyślij ``text`` (Markdown) jako wiadomość 1:1 do użytkownika o danym AAD id."""
        ...

    async def post_channel(self, team_id: str, channel_id: str, text: str) -> None:
        """Wyślij ``text`` (Markdown) jako NOWY post (wątek root) na kanale zespołu."""
        ...
