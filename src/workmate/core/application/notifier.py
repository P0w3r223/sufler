"""Notifier zdarzeń → Teams (EventStore → dual-target push, ADR 0022) — warstwa spajająca.

Czyta nowe zdarzenia z magazynu (kursor po ``id``) i wypycha je do skonfigurowanych celów
Teams (czat 1:1 i/lub kanał). Zależy tylko od portów (``EventService``, ``TeamsNotifier``) —
testowalny na atrapach bez sieci. Semantyka „co najmniej raz": kursor przesuwamy DOPIERO po
udanej wysyłce, więc błąd transportu daje ponowienie (kosztem ewentualnego dubla) zamiast utraty.

Domyślnie notyfikujemy WYŁĄCZNIE zdarzenia ``source="github"`` — to element strażnika pętli:
zdarzenia pochodzące z Teams (np. issue utworzone komendą) nie wracają jako powiadomienie.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workmate.core.application.events import EventService
    from workmate.core.domain.events import Event
    from workmate.core.ports.notifications import TeamsNotifier

logger = logging.getLogger(__name__)

_KIND_LABELS = {
    "issue_opened": "Nowe issue",
    "issue_comment": "Nowy komentarz",
}


@dataclass(frozen=True)
class NotifyTargets:
    """Cele powiadomień (oba konfigurowalne, ADR 0022): czat 1:1 i/lub kanał zespołu."""

    chat_user_id: str = ""
    team_id: str = ""
    channel_id: str = ""
    enable_chat: bool = False
    enable_channel: bool = False


def default_event_render(event: Event) -> str:
    """Zwięzła wiadomość Markdown dla Teams z pól zdarzenia (adapter zamieni ją na HTML).

    Treść (tytuł/skrót/autor) pochodzi z GitHuba i jest DANYMI — sanityzację zrobił już
    ``EventService.ingest`` (brak znaków sterujących), a adapter Teams zescapuje surowy HTML.
    """
    label = _KIND_LABELS.get(event.kind, event.kind)
    parts = [f"**[GitHub] {label}**"]
    if event.title:
        parts.append(event.title)
    if event.summary:
        parts.append(event.summary)
    if event.actor:
        parts.append(f"— {event.actor}")
    if event.url:
        parts.append(event.url)
    return "\n\n".join(parts)


class EventNotifier:
    """Pompuje nowe zdarzenia z magazynu do Teams (dual-target) z kursorem at-least-once."""

    def __init__(
        self,
        events: EventService,
        sender: TeamsNotifier,
        *,
        targets: NotifyTargets,
        save_cursor: Callable[[int], None],
        cursor: int = 0,
        source: str = "github",
        poll_interval: int = 60,
        render: Callable[[Event], str] = default_event_render,
    ) -> None:
        self._events = events
        self._sender = sender
        self._targets = targets
        self._save_cursor = save_cursor
        self._cursor = cursor
        self._source = source
        self._poll_interval = poll_interval
        self._render = render

    async def pump(self) -> None:
        """Pętla: co ``poll_interval`` wypchnij nowe zdarzenia; błąd rundy nie kładzie pętli."""
        while True:
            try:
                await self.pump_once()
            except Exception:
                logger.exception("Błąd wypychania zdarzeń do Teams — ponowię za chwilę")
            await asyncio.sleep(self._poll_interval)

    async def pump_once(self) -> int:
        """Wyślij zdarzenia nowsze niż kursor; zwróć liczbę wypchniętych. Kursor po sukcesie."""
        batch = self._events.read_since(self._cursor, source=self._source, limit=50)
        sent = 0
        for event in batch:
            await self._deliver(event)
            # Kursor przesuwamy DOPIERO po udanej wysyłce (at-least-once): błąd transportu
            # zostawia kursor, więc następna runda ponowi zamiast zgubić zdarzenie.
            self._cursor = event.id
            self._save_cursor(event.id)
            sent += 1
        return sent

    async def _deliver(self, event: Event) -> None:
        """Wyślij zdarzenie do WŁĄCZONYCH celów (czat 1:1 i/lub kanał)."""
        text = self._render(event)
        targets = self._targets
        if targets.enable_chat and targets.chat_user_id:
            await self._sender.send_chat(targets.chat_user_id, text)
        if targets.enable_channel and targets.team_id and targets.channel_id:
            await self._sender.post_channel(targets.team_id, targets.channel_id, text)
