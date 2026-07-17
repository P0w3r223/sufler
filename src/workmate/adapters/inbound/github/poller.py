"""Pętla pollingu GitHub (tryb delegowany PAT) na wstrzykniętym porcie read + ``EventService``.

Wolna od ``httpx``: klient GitHub (port ``GithubReadPort``, SYNCHRONICZNY) wołany jest w puli
wątków (jak teams_graph odświeża sync MSAL), więc pełną pętlę testujemy atrapą portu bez sieci.
Wszystkie decyzje (mapowanie, self-skip, watermark) delegujemy do czystej ``selection`` — tu
zostaje orkiestracja I/O i utrwalanie stanu. Deduplikację egzekwuje magazyn (at-least-once):
watermark ``since`` przesuwamy PO ingest, a ponowne przyjęcie tego samego zdarzenia jest no-op.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from functools import partial
from typing import TYPE_CHECKING, Any

from workmate.adapters.inbound.github import selection
from workmate.core.errors import WriteError

if TYPE_CHECKING:
    from workmate.core.application.events import EventService
    from workmate.core.domain.events import NewEvent
    from workmate.core.ports.github import GithubReadPort

logger = logging.getLogger(__name__)

# Cap PR-ów odpytywanych o recenzje na rundę — endpoint recenzji jest per-PR i bez ``since``,
# więc bez capa koszt API rósłby liniowo z liczbą otwartych PR (ochrona limitu 5000/h).
_MAX_REVIEW_PRS = 20


def _utcnow() -> datetime:
    """Bieżąca chwila jako aware UTC — do watermarku startowego (ignoruj backlog sprzed startu)."""
    return datetime.now(timezone.utc)


class GithubPoller:
    """Nasłuch repo GitHub (delegowany PAT). Zdarzenia → ``EventService.ingest`` (dedup magazynu).

    ``state`` to mutowalny słownik utrwalany przez ``persist`` po każdej rundzie: ``issues_since``
    i ``comments_since`` (watermark ``since`` per zasób). ``self_login`` (konto PAT) do self-skip
    ustala się z konfiguracji albo z ``authenticated_login`` na starcie.
    """

    def __init__(
        self,
        client: GithubReadPort,
        events: EventService,
        *,
        owner: str,
        repo: str,
        watch_kinds: tuple[str, ...],
        state: dict[str, Any],
        persist: Callable[[dict[str, Any]], None],
        poll_interval: int,
        per_page: int,
        self_login: str = "",
        project: str = "",
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._client = client
        self._events = events
        self._owner = owner
        self._repo = repo
        self._watch_kinds = watch_kinds
        self._state = state
        self._persist = persist
        self._poll_interval = poll_interval
        self._per_page = per_page
        self._self_login = self_login
        self._project = project
        self._clock = clock

    async def run(self) -> None:
        """Pętla główna: co ``poll_interval`` odpytaj repo o nowe issue/komentarze i przyjmij je."""
        await self._resolve_self_login()
        # Seed w formacie GitHuba (``…Z``), NIE ``isoformat`` (``+00:00``): watermarki CI/recenzji
        # są porównywane leksykograficznie ze znacznikami GitHuba, więc format musi być zgodny.
        self._seed(_iso_z(self._clock()))
        logger.info(
            "Nasłuch GitHub %s/%s (delegowany PAT, konto=%s). Ctrl+C kończy.",
            self._owner,
            self._repo,
            self._self_login or "?",
        )
        while True:
            try:
                await self.poll_once()
            except Exception:
                # Błąd rundy (sieć/limit/kształt) nie kładzie pętli — ponowimy za chwilę.
                logger.exception("Błąd pollingu GitHub %s/%s", self._owner, self._repo)
            await asyncio.sleep(self._poll_interval)

    async def poll_once(self) -> int:
        """Jedna runda: pobierz, zmapuj/przefiltruj, przyjmij do magazynu, przesuń watermark.

        Zwraca liczbę NOWO przyjętych zdarzeń (po dedupie) — wygodne do testów i logu.
        """
        loop = asyncio.get_running_loop()
        raw_issues: list[dict[str, Any]] = []
        raw_comments: list[dict[str, Any]] = []
        raw_runs: list[dict[str, Any]] = []
        raw_reviews: list[dict[str, Any]] = []
        # ``/issues`` obejmuje ISSUE i PR (te mają klucz ``pull_request``) — pobieramy, gdy
        # nasłuchujemy któregokolwiek; selection rozróżni je po ``watch_kinds``.
        if "issues" in self._watch_kinds or "pulls" in self._watch_kinds:
            since = _parse_since(self._state.get("issues_since"))
            raw_issues = await loop.run_in_executor(
                None,
                lambda: self._client.list_issues(
                    self._owner, self._repo, since=since, per_page=self._per_page
                ),
            )
        if "comments" in self._watch_kinds:
            since = _parse_since(self._state.get("comments_since"))
            raw_comments = await loop.run_in_executor(
                None,
                lambda: self._client.list_issue_comments(
                    self._owner, self._repo, since=since, per_page=self._per_page
                ),
            )
        if "ci" in self._watch_kinds:
            raw_runs = await loop.run_in_executor(
                None,
                lambda: self._client.list_workflow_runs(
                    self._owner, self._repo, per_page=self._per_page
                ),
            )
        if "reviews" in self._watch_kinds:
            raw_reviews = await self._fetch_reviews(loop, raw_issues)

        events = selection.select_events(
            raw_issues,
            raw_comments,
            raw_runs,
            raw_reviews,
            self_login=self._self_login,
            watch_kinds=self._watch_kinds,
            runs_since=self._state.get("runs_since", ""),
            reviews_since=self._state.get("reviews_since", ""),
            repo=f"{self._owner}/{self._repo}",
            project=self._project,
        )
        # Ingest (SQLite, synchroniczny) offloadujemy do puli wątków — nie blokujemy pętli, więc
        # współbieżny notifier działa dalej. Błąd JEDNEGO zdarzenia izolujemy per zdarzenie (patrz
        # ``_ingest_batch``), żeby jedno zatrute nie zakleszczyło całego strumienia GitHub → Teams.
        ingested = await loop.run_in_executor(None, self._ingest_batch, events)

        # Watermark przesuwamy DOPIERO po ingest (at-least-once): gdyby proces padł wcześniej,
        # następna runda ponowi, a dedup magazynu pominie już przyjęte.
        self._state["issues_since"] = selection.next_since(
            raw_issues, self._state.get("issues_since", "")
        )
        self._state["comments_since"] = selection.next_since(
            raw_comments, self._state.get("comments_since", "")
        )
        self._state["runs_since"] = selection.next_since(
            raw_runs, self._state.get("runs_since", "")
        )
        self._state["reviews_since"] = selection.next_since(
            raw_reviews, self._state.get("reviews_since", ""), field="submitted_at"
        )
        self._persist(self._state)
        if ingested:
            logger.info("Przyjęto %d zdarzeń z GitHub %s/%s", ingested, self._owner, self._repo)
        return ingested

    def _ingest_batch(self, events: list[NewEvent]) -> int:
        """Przyjmij zdarzenia do magazynu, IZOLUJĄC błąd per zdarzenie; zwróć liczbę nowych.

        ``EventService.ingest`` odrzuca (``WriteError``) zdarzenie ze znakiem sterującym — to
        prawidłowe u ŹRÓDŁA zapisu, ale w PASYWNYM pollingu cudzej treści jedno takie zdarzenie
        NIE może wywrócić całej rundy (watermark by nie ruszył i most milczałby w kółko). Więc
        łapiemy je per pozycja: pomijamy zatrute, resztę przyjmujemy, watermark i tak się przesunie.
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

    async def _fetch_reviews(
        self, loop: asyncio.AbstractEventLoop, raw_issues: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Pobierz recenzje OTWARTYCH PR widzianych w tej rundzie (cap ``_MAX_REVIEW_PRS``).

        Endpoint recenzji jest per-PR i bez ``since`` — ograniczamy się do PR-ów zaktualizowanych
        w bieżącym oknie ``/issues`` (te są w ``raw_issues``) i twardym capem. Watermark
        ``reviews_since`` (w selection) odsiewa już widziane, a dedup magazynu domyka poprawność.
        """
        numbers = [
            raw["number"]
            for raw in raw_issues
            if raw.get("pull_request")
            and raw.get("state") == "open"
            and raw.get("number") is not None
        ][:_MAX_REVIEW_PRS]
        reviews: list[dict[str, Any]] = []
        for number in numbers:
            batch = await loop.run_in_executor(
                None,
                partial(self._client.list_pull_reviews, self._owner, self._repo, number),
            )
            reviews.extend(batch)
        return reviews

    async def _resolve_self_login(self) -> None:
        """Ustal login konta PAT (self-skip), jeśli nie podano w konfiguracji — best-effort."""
        if self._self_login:
            return
        loop = asyncio.get_running_loop()
        try:
            self._self_login = await loop.run_in_executor(None, self._client.authenticated_login)
        except Exception:
            logger.warning(
                "Nie udało się ustalić konta PAT — self-skip wyłączony (ryzyko pętli self-ping)."
            )

    def _seed(self, startup_iso: str) -> None:
        """Zainicjuj watermarki = teraz (idempotentnie): ignoruj backlog sprzed uruchomienia.

        Dotyczy WSZYSTKICH zasobów, w tym CI i recenzji (których endpointy nie mają ``since``) —
        inaczej pierwsza runda zalałaby Teams starymi przebiegami/recenzjami sprzed startu.
        """
        self._state.setdefault("issues_since", startup_iso)
        self._state.setdefault("comments_since", startup_iso)
        self._state.setdefault("runs_since", startup_iso)
        self._state.setdefault("reviews_since", startup_iso)


def _parse_since(value: Any) -> datetime | None:
    """Watermark ISO z stanu → aware UTC dla parametru ``since``; puste → ``None`` (bez filtra)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso_z(when: datetime) -> str:
    """Znacznik w formacie GitHuba — ISO 8601 UTC z ``Z`` (spójny z ``updated_at``/``submitted``).

    Do seedu watermarków: dla CI/recenzji watermark porównujemy leksykograficznie wprost ze
    znacznikami GitHuba (``…Z``), więc seed w tym samym formacie eliminuje ryzyko rozjazdu.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
