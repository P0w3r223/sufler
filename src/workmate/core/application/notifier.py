"""Notifier zdarzeń → Teams (EventStore → dual-target push, ADR 0022) — warstwa spajająca.

Czyta nowe zdarzenia z magazynu (kursor po ``id``) i wypycha je do skonfigurowanych celów
Teams (czat 1:1 i/lub kanał). Zależy tylko od portów (``EventService``, ``TeamsNotifier``) —
testowalny na atrapach bez sieci. Semantyka „co najmniej raz": kursor przesuwamy DOPIERO po
udanej wysyłce, więc błąd transportu daje ponowienie (kosztem ewentualnego dubla) zamiast utraty.

Konsumowane źródło jest KONFIGUROWALNE (parametr ``source``, domyślnie ``github``); drzwi Jira
uruchamiają własny notifier z ``source="jira"`` (osobny proces, osobny kursor). To zarazem element
strażnika pętli: zdarzenia z Teams (np. issue utworzone komendą) nie wracają jako powiadomienie.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from workmate.core.domain.threads import resolve_thread_target
from workmate.core.errors import ThreadRootGone

if TYPE_CHECKING:
    from workmate.core.application.events import EventService
    from workmate.core.domain.events import Event
    from workmate.core.ports.notifications import TeamsNotifier
    from workmate.core.ports.thread_links import ThreadLinkStore

logger = logging.getLogger(__name__)

_KIND_LABELS = {
    "issue_opened": "Nowe issue",
    "issue_comment": "Nowy komentarz",
    "pr_opened": "Nowy PR",
    "pr_merged": "PR zmergowany",
    "pr_closed": "PR zamknięty",
    "pr_comment": "Nowy komentarz w PR",
    "pr_review": "Recenzja PR",
    "ci_success": "CI: sukces",
    "ci_failure": "CI: porażka",
    "branch_pushed": "Push do gałęzi",
    "branch_deleted": "Usunięto gałąź",
    # Jira (ADR 0030): utworzenie zgłoszenia, zmiana statusu, komentarz.
    "jira_issue_created": "Nowe zgłoszenie",
    "jira_transition": "Zmiana statusu",
    "jira_comment": "Nowy komentarz",
}

# Etykieta źródła w nagłówku wiadomości — wyprowadzana z ``event.source`` (nie zaszyta), żeby
# most obsługiwał wiele źródeł (GitHub, Jira) tym samym renderem (ADR 0030).
_SOURCE_LABELS = {"github": "GitHub", "jira": "Jira", "teams": "Teams"}


def _source_label(source: str) -> str:
    """Ładna etykieta źródła (``github`` → ``GitHub``); nieznane źródło → surowa wartość."""
    return _SOURCE_LABELS.get(source, source or "?")


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

    Treść (tytuł/skrót/autor) pochodzi ze źródła (GitHub/Jira) i jest DANYMI — sanityzację zrobił
    już ``EventService.ingest`` (brak znaków sterujących), a adapter Teams zescapuje surowy HTML.
    """
    label = _KIND_LABELS.get(event.kind, event.kind)
    parts = [f"**[{_source_label(event.source)}] {label}**"]
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
        thread_links: ThreadLinkStore | None = None,
    ) -> None:
        self._events = events
        self._sender = sender
        self._targets = targets
        self._save_cursor = save_cursor
        self._cursor = cursor
        self._source = source
        self._poll_interval = poll_interval
        self._render = render
        # Gdy podano ``thread_links`` (ADR 0024, flaga wątkowania ON), zdarzenia tego samego
        # issue/PR lecą do JEDNEGO wątku na kanale; gdy ``None`` — każde jako nowy root (jak dziś).
        self._thread_links = thread_links

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
            await self._deliver_channel(event, text)

    async def _deliver_channel(self, event: Event, text: str) -> None:
        """Wyślij na kanał: gdy wątkowanie ON i zdarzenie ma cel — do wątku celu, inaczej nowy root.

        Wątkowanie kluczujemy na CELU (issue/PR), nie na rodzaju zdarzenia: dzięki temu komentarz
        czy CI trafia do wątku „opened", a jeśli root jeszcze nie istnieje (np. komentarz dotarł
        pierwszy) — tworzymy go teraz i zapamiętujemy. Zdarzenie bez celu (np. CI bez PR) idzie jako
        osobny root. Przy ``thread_links=None`` (wątkowanie OFF) — zawsze nowy root (jak dziś).
        """
        targets = self._targets
        target = resolve_thread_target(event.url) if self._thread_links is not None else None
        if self._thread_links is None or target is None:
            await self._sender.post_channel(targets.team_id, targets.channel_id, text)
            return
        kind, number = target
        root_id = self._thread_links.get_root(targets.team_id, targets.channel_id, kind, number)
        if root_id:
            try:
                await self._sender.reply_channel(targets.team_id, targets.channel_id, root_id, text)
                return
            except ThreadRootGone:
                # Root usunięty w Teams — NIE blokuj całego strumienia na tym zdarzeniu: schodzimy
                # do utworzenia nowego roota i PRZEŁĄCZENIA linku (``link`` nadpisuje nieaktualny).
                logger.info(
                    "Root wątku %s zniknął — tworzę nowy i przełączam link (%s #%s)",
                    root_id,
                    kind,
                    number,
                )
        new_root = await self._sender.post_channel(targets.team_id, targets.channel_id, text)
        if new_root:
            self._thread_links.link(targets.team_id, targets.channel_id, kind, number, new_root)
