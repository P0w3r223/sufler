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

from sufler.core.domain.threads import resolve_thread_target
from sufler.core.errors import ThreadRootGone

if TYPE_CHECKING:
    from sufler.core.application.events import EventService
    from sufler.core.domain.events import Event
    from sufler.core.ports.dead_letters import DeadLetterStore
    from sufler.core.ports.notifications import TeamsNotifier
    from sufler.core.ports.thread_links import ThreadLinkStore

logger = logging.getLogger(__name__)

_KIND_LABELS = {
    "issue_opened": "Nowe issue",
    "issue_closed": "Issue zamknięte",
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
    # NIKT już nie tworzy tego zdarzenia — ścieżkę zapisu worklogu wycięto (ADR 0034 → 0035).
    # Etykieta zostaje, bo ``events.db`` jest APPEND-ONLY: wpisy sprzed cięcia nadal tam są
    # i muszą się renderować. To nie jest martwy kod, tylko obsługa historii.
    "jira_worklog": "Wpis czasu pracy",
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
        dead_letters: DeadLetterStore | None = None,
        heartbeat: Callable[[], None] | None = None,
        max_attempts: int = 5,
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
        # Kwarantanna zdarzeń niewysyłalnych (ADR 0067 §2); ``None`` → dawne zachowanie (ponawiaj
        # w nieskończoność, zdarzenie blokuje strumień). Puls notifiera (odrębny od pulsu pollera);
        # ``None`` → brak pulsu. ``max_attempts`` prób na zdarzenie przed dead-letter (rek. 2: 5).
        self._dead_letters = dead_letters
        self._heartbeat = heartbeat
        self._max_attempts = max_attempts
        # Licznik prób per zdarzenie — W PAMIĘCI: po restarcie rusza od zera (świadomie ograniczone
        # dodatkowe ponowienia zamiast persystencji licznika, ADR 0067 R2).
        self._attempts: dict[int, int] = {}

    async def pump(self) -> None:
        """Pętla: co ``poll_interval`` wypchnij nowe zdarzenia; błąd rundy nie kładzie pętli."""
        while True:
            try:
                await self.pump_once()
            except Exception:
                logger.exception("Błąd wypychania zdarzeń do Teams — ponowię za chwilę")
            await asyncio.sleep(self._poll_interval)

    async def pump_once(self) -> int:
        """Wyślij zdarzenia nowsze niż kursor; zwróć liczbę zdarzeń, które RUSZYŁY kursor
        (dostarczone lub przeniesione do kwarantanny).

        Kursor przesuwamy DOPIERO po udanej wysyłce (at-least-once, ADR 0022). Zdarzenia, którego
        nie da się wysłać, ponawiamy w kolejnych rundach; po ``max_attempts`` próbach przenosimy je
        do ``dead_letters`` i DOPIERO POTEM ruszamy kursor — poison message nie blokuje strumienia,
        a zapis-przed-przesunięciem zachowuje at-least-once (crash pomiędzy = ponowienie).
        Puls notifiera bijemy po rundzie PRODUKTYWNEJ: dostarczono ≥1, przeniesiono do dead-letter,
        albo pusta kolejka. Runda przerwana ponowieniem poniżej progu bije TYLKO gdy wcześniej
        ruszyła kursor (``sent > 0`` — realny postęp mimo zatoru na dalszym zdarzeniu); runda bez
        żadnego postępu (zator już na pierwszym zdarzeniu) NIE bije — to sygnał zatoru dla
        healthchecku.
        """
        batch = self._events.read_since(self._cursor, source=self._source, limit=50)
        sent = 0
        for event in batch:
            try:
                await self._deliver(event)
            except Exception as exc:
                if self._dead_letters is None:
                    # Brak magazynu kwarantanny → dawne zachowanie: zostaw kursor i PRZERWIJ rundę,
                    # zdarzenie ponowi się w następnej (at-least-once). Bez licznika prób — poison
                    # message blokuje strumień jak przed ADR 0067 (produkcja zawsze wpina magazyn).
                    logger.warning("Nie udało się wysłać zdarzenia %s — ponowię", event.id)
                    self._beat_if_progress(sent)
                    return sent
                attempts = self._attempts.get(event.id, 0) + 1
                self._attempts[event.id] = attempts
                if attempts < self._max_attempts:
                    # Poniżej progu → zostaw kursor i PRZERWIJ rundę; kolejne czekają za tym
                    # zdarzeniem. Brak pulsu w tej rundzie jest zamierzony (sygnał zatoru, §2).
                    logger.warning(
                        "Nie udało się wysłać zdarzenia %s (próba %d/%d) — ponowię",
                        event.id,
                        attempts,
                        self._max_attempts,
                    )
                    self._beat_if_progress(sent)
                    return sent
                # Próg osiągnięty: kwarantanna PRZED przesunięciem kursora (zapis-przed-ruchem).
                self._dead_letters.record(
                    source=self._source,
                    event_id=event.id,
                    reason=repr(exc),
                    attempts=attempts,
                )
                logger.error(
                    "Zdarzenie %s trwale niewysyłalne po %d próbach — dead-letter, kursor dalej",
                    event.id,
                    attempts,
                )
                self._attempts.pop(event.id, None)
            else:
                self._attempts.pop(event.id, None)  # sukces — wyczyść licznik prób tego zdarzenia
            self._cursor = event.id
            self._save_cursor(event.id)
            sent += 1
        if self._heartbeat is not None:
            self._heartbeat()
        return sent

    def _beat_if_progress(self, sent: int) -> None:
        """Puls przy wczesnym wyjściu z rundy TYLKO gdy ruszyliśmy kursor (``sent > 0``).

        Runda przerwana ponowieniem poniżej progu wciąż jest PRODUKTYWNA, jeśli wcześniej
        dostarczyła/zdead-letterowała choć jedno zdarzenie — inaczej healthcheck raportowałby
        ``unhealthy`` przy nadrabianiu zaległości mimo realnego postępu. Zator (``sent == 0``,
        pierwsze zdarzenie się nie udaje) świadomie NIE bije — to sygnał braku postępu.
        """
        if sent and self._heartbeat is not None:
            self._heartbeat()

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
