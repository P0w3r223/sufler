"""Pętla pollingu Jiry (tryb delegowany PAT) na wstrzykniętym porcie read + ``EventService``.

Wolna od ``httpx``: klient Jiry (port ``JiraReadPort``, SYNCHRONICZNY) wołany jest w puli wątków
(jak github/teams_graph wołają sync klienty), więc pełną pętlę testujemy atrapą portu bez sieci.
Wszystkie decyzje (mapowanie, self-skip, budowa JQL, watermark) delegujemy do czystej ``selection``
— tu zostaje orkiestracja I/O i utrwalanie stanu. Deduplikację egzekwuje magazyn (at-least-once):
watermark ``issues_since`` przesuwamy PO ingest, a ponowne przyjęcie tego samego zdarzenia to no-op.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from workmate.adapters.inbound.jira import selection
from workmate.core.errors import WriteError

if TYPE_CHECKING:
    from workmate.core.application.events import EventService
    from workmate.core.domain.events import NewEvent
    from workmate.core.ports.jira import JiraReadPort

logger = logging.getLogger(__name__)


def _now_local() -> datetime:
    """Bieżąca chwila jako aware ``datetime`` w strefie LOKALNEJ maszyny — do seedu watermarku.

    Seed zapisujemy jako aware ISO; ``build_jql`` degraduje go do minutowego JQL (który Jira
    interpretuje w strefie zalogowanego użytkownika), a ``select_events`` używa jako granicy filtra
    świeżości. Zakładamy, że maszyna pollera i użytkownik Jiry są w tej samej strefie (prawdziwe dla
    wewnętrznego pilotażu on-prem) — po pierwszym issue watermark i tak przechodzi na znaczniki Jiry
    (samostrojenie strefy). Nadpisywalne wstrzyknięciem ``clock``.
    """
    return datetime.now().astimezone()


class JiraPoller:
    """Nasłuch projektów Jira (delegowany PAT). Zdarzenia → ``EventService.ingest`` (dedup store).

    ``state`` to mutowalny słownik utrwalany przez ``persist`` po każdej rundzie: ``issues_since``
    (watermark JQL ``updated``). ``self_account`` (konto PAT) do self-skip ustala się z konfiguracji
    albo z ``authenticated_account`` na starcie. ``project_map`` mapuje klucz projektu Jira na klucz
    projektu WorkMate (ADR 0028) — selection stempluje ``event.project`` per issue.
    """

    def __init__(
        self,
        client: JiraReadPort,
        events: EventService,
        *,
        base_url: str,
        watch_projects: tuple[str, ...],
        state: dict[str, Any],
        persist: Callable[[dict[str, Any]], None],
        poll_interval: int,
        per_page: int,
        self_account: str = "",
        project_map: dict[str, str] | None = None,
        clock: Callable[[], datetime] = _now_local,
    ) -> None:
        self._client = client
        self._events = events
        self._base_url = base_url
        self._watch_projects = watch_projects
        self._state = state
        self._persist = persist
        self._poll_interval = poll_interval
        self._per_page = per_page
        self._self_account = self_account
        self._project_map = project_map or {}
        self._clock = clock

    async def run(self) -> None:
        """Pętla główna: co ``poll_interval`` odpytaj Jirę o nowe/zmienione issue i przyjmij je."""
        await self._resolve_self_account()
        self._seed(self._clock().isoformat())
        logger.info(
            "Nasłuch Jira %s (projekty=%s, delegowany PAT, konto=%s). Ctrl+C kończy.",
            self._base_url,
            ", ".join(self._watch_projects) or "?",
            self._self_account or "?",
        )
        while True:
            try:
                await self.poll_once()
            except Exception:
                # Błąd rundy (sieć/JQL/kształt) nie kładzie pętli — ponowimy za chwilę.
                logger.exception("Błąd pollingu Jira %s", self._base_url)
            await asyncio.sleep(self._poll_interval)

    async def poll_once(self) -> int:
        """Jedna runda: zbuduj JQL, pobierz issue, zmapuj/przefiltruj, przyjmij, przesuń watermark.

        Zwraca liczbę NOWO przyjętych zdarzeń (po dedupie) — wygodne do testów i logu.
        """
        loop = asyncio.get_running_loop()
        since = self._state.get("issues_since", "")
        jql = selection.build_jql(self._watch_projects, since)
        raw_issues = await loop.run_in_executor(
            None, lambda: self._client.search_issues(jql, max_results=self._per_page)
        )

        events = selection.select_events(
            raw_issues,
            self_account=self._self_account,
            base_url=self._base_url,
            project_map=self._project_map,
            since=since,
        )
        # Ingest (SQLite, synchroniczny) offloadujemy do puli wątków — nie blokujemy pętli, więc
        # współbieżny notifier działa dalej. Błąd JEDNEGO zdarzenia izolujemy per zdarzenie
        # (``_ingest_batch``), żeby jedno zatrute nie zakleszczyło całego strumienia Jira → Teams.
        ingested = await loop.run_in_executor(None, self._ingest_batch, events)

        # Watermark przesuwamy DOPIERO po ingest (at-least-once): gdyby proces padł wcześniej,
        # następna runda ponowi, a dedup magazynu pominie już przyjęte.
        self._state["issues_since"] = selection.next_since(
            raw_issues, self._state.get("issues_since", "")
        )
        self._persist(self._state)
        if ingested:
            logger.info("Przyjęto %d zdarzeń z Jira %s", ingested, self._base_url)
        return ingested

    def _ingest_batch(self, events: list[NewEvent]) -> int:
        """Przyjmij zdarzenia do magazynu, IZOLUJĄC błąd per zdarzenie; zwróć liczbę nowych.

        ``EventService.ingest`` odrzuca (``WriteError``) zdarzenie ze znakiem sterującym — to
        prawidłowe u ŹRÓDŁA zapisu, ale w PASYWNYM pollingu cudzej treści jedno takie zdarzenie NIE
        może wywrócić całej rundy (watermark by nie ruszył i most milczałby w kółko). Łapiemy je
        per pozycja: pomijamy zatrute, resztę przyjmujemy, watermark i tak się przesunie.
        """
        count = 0
        for event in events:
            try:
                if self._events.ingest(event) is not None:
                    count += 1
            except WriteError:
                logger.warning(
                    "Pominięto zatrute zdarzenie %s/%s (niedozwolony znak w treści).",
                    event.source,
                    event.external_id,
                )
        return count

    async def _resolve_self_account(self) -> None:
        """Ustal konto PAT (self-skip), jeśli nie podano w konfiguracji — best-effort."""
        if self._self_account:
            return
        loop = asyncio.get_running_loop()
        try:
            self._self_account = await loop.run_in_executor(
                None, self._client.authenticated_account
            )
        except Exception:
            logger.warning(
                "Nie udało się ustalić konta PAT Jira — self-skip wyłączony (ryzyko pętli)."
            )

    def _seed(self, startup_jql_datetime: str) -> None:
        """Zainicjuj watermark = teraz (idempotentnie): ignoruj backlog sprzed uruchomienia.

        Bez seedu pierwsza runda zalałaby Teams całą historią projektów. Po pierwszym issue
        watermark przechodzi na znaczniki Jiry (``next_since``) — rozjazd strefy seedu jest krótki.
        """
        self._state.setdefault("issues_since", startup_jql_datetime)
