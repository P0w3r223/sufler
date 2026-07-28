"""Pętla pollingu kanałów Teams (tryb delegowany) na wstrzykniętym porcie Graph.

Wolna od ``httpx``/``msal``: klient Graph (port ``GraphChannelClient``) i handler są
wstrzykiwane, więc pełną pętlę testujemy atrapą portu bez sieci. Wszystkie decyzje
(co nowe, watermark per wątek, dedup, self-skip) delegujemy do czystej
``selection.plan_channel`` — tu zostaje tylko orkiestracja I/O i utrwalanie stanu.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Protocol

from workmate.adapters.inbound.teams_graph import selection
from workmate.adapters.inbound.teams_graph.selection import ChannelMessage

if TYPE_CHECKING:
    from workmate.adapters.inbound.teams_graph.attachments import AttachmentMaterializer

logger = logging.getLogger(__name__)

# Górna granica listy dedup (id odpisanych wiadomości) — chroni przed nieskończonym wzrostem.
_REPLIED_CAP = 500
# Przerwa między kanałami w jednej rundzie — respektuje limit zapytań na kanał.
_INTER_CHANNEL_SLEEP_S = 1


def _utcnow() -> datetime:
    """Bieżąca chwila jako aware UTC — spójna z parsowaniem znaczników Graph (selection)."""
    return datetime.now(timezone.utc)


def _status_of(exc: BaseException) -> int | None:
    """Kod HTTP z wyjątku klienta Graph (duck typing — bez zależności od httpx w pętli)."""
    return getattr(getattr(exc, "response", None), "status_code", None)


def _is_permanent(exc: BaseException) -> bool:
    """Czy błąd wysyłki jest TRWAŁY (4xx poza 429) — nie ma sensu go ponawiać."""
    status = _status_of(exc)
    return status is not None and 400 <= status < 500 and status != 429


class GraphChannelClient(Protocol):
    """Port drzwi na Microsoft Graph — tylko to, czego potrzebuje pętla pollingu.

    Konkretną implementację (``graph.HttpxGraphChannelClient``) wstrzykuje ``app.py``;
    testy podają atrapę spełniającą ten protokół strukturalnie.
    """

    async def refresh_auth(self) -> None: ...

    async def get_me_id(self) -> str: ...

    async def list_root_messages(
        self, team_id: str, channel_id: str, *, top: int
    ) -> list[dict[str, Any]]: ...

    async def list_replies(
        self, team_id: str, channel_id: str, root_id: str, *, top: int
    ) -> list[dict[str, Any]]: ...

    async def post_reply(self, team_id: str, channel_id: str, root_id: str, text: str) -> None: ...

    async def get_hosted_content(
        self, team_id: str, channel_id: str, root_id: str, message_id: str, hosted_id: str
    ) -> bytes: ...

    async def download_shared_url(self, url: str) -> bytes: ...

    async def download_public_url(self, url: str) -> bytes: ...


HandleMessage = Callable[[ChannelMessage, str], Awaitable[str | None]]


class ChannelPoller:
    """Nasłuch kanałów Teams (delegowany Graph). Decyzje deleguje do ``selection``.

    ``state`` to mutowalny słownik utrwalany przez ``persist`` po każdej rundzie; jego
    kształt inicjuje ``_seed`` (per kanał: ``since_roots`` + ``threads``; globalnie:
    ``replied``). Zegar (``clock``) jest wstrzykiwalny, żeby testy sterowały czasem
    (eksmisja martwych wątków).
    """

    def __init__(
        self,
        client: GraphChannelClient,
        handle: HandleMessage,
        *,
        watch: tuple[tuple[str, str], ...],
        state: dict[str, Any],
        persist: Callable[[dict[str, Any]], None],
        top_roots: int,
        top_replies: int,
        poll_interval: int,
        active_idle: timedelta,
        clock: Callable[[], datetime] = _utcnow,
        materializer: AttachmentMaterializer | None = None,
        stop: asyncio.Event | None = None,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        self._client = client
        self._handle = handle
        self._watch = watch
        self._state = state
        self._persist = persist
        self._top_roots = top_roots
        self._top_replies = top_replies
        self._poll_interval = poll_interval
        self._active_idle = active_idle
        self._clock = clock
        # Materializacja załączników (I/O) — ``None`` wyłącza obsługę plików/obrazów
        # (drzwi tekstowe, testy bez sieci); wpięta w ``app.py`` na kliencie Graph.
        self._materializer = materializer
        self._stop = stop
        self._heartbeat = heartbeat

    async def run(self) -> None:
        """Pętla główna: co ``poll_interval`` odpytaj każdy kanał i odpowiedz na nowe wpisy.

        Sygnał ``stop`` (SIGTERM w ``app.py``) kończy pętlę PO utrwaleniu bieżącej rundy —
        graceful shutdown: bieżąca runda kanałów dochodzi do zapisu, zanim proces wyjdzie (R1).
        """
        await self._client.refresh_auth()
        me_id = await self._client.get_me_id()
        # Watermark startowy = teraz: nie odpowiadamy na backlog sprzed uruchomienia.
        self._seed(self._clock().isoformat())
        logger.info(
            "Nasłuch %d kanałów Teams (delegowany, jako user_id=%s). Ctrl+C/SIGTERM kończy.",
            len(self._watch),
            me_id,
        )
        while not self._stopping():
            try:
                await self._client.refresh_auth()
            except Exception:
                # Odświeżenie tokenu padło → NIE bijemy pulsu (jałowa pętla auth), healthcheck
                # po wieku pulsu wykryje token, którego nie da się odnowić bez re-primingu.
                logger.exception("Nie udało się odświeżyć tokenu Graph — ponowię za chwilę")
                if await self._sleep_or_stop():
                    break
                continue
            for team_id, channel_id in self._watch:
                try:
                    await self._poll_channel(team_id, channel_id, me_id)
                except Exception:
                    # Błąd jednego kanału nie kładzie pozostałych ani całej pętli.
                    logger.exception("Błąd pollingu kanału %s/%s", team_id, channel_id)
                await asyncio.sleep(_INTER_CHANNEL_SLEEP_S)
            self._persist(self._state)
            # Puls PO domkniętej rundzie kanałów (R5) — auth odświeżone i stan utrwalony.
            self._beat()
            if await self._sleep_or_stop():
                break
        logger.info("Drzwi Teams: zatrzymanie na sygnał, stan zapisany.")

    def _beat(self) -> None:
        """Odśwież puls żywotności, jeśli wstrzyknięto (R5). Bez callbacku — no-op (dev/testy)."""
        if self._heartbeat is not None:
            self._heartbeat()

    def _stopping(self) -> bool:
        """True, gdy ``app.py`` ustawił ``stop`` (SIGTERM/SIGINT) — pętla ma się zakończyć."""
        return self._stop is not None and self._stop.is_set()

    async def _sleep_or_stop(self) -> bool:
        """Czekaj ``poll_interval`` albo do sygnału stop; zwróć True, gdy stop (przerwij pętlę)."""
        if self._stop is None:
            await asyncio.sleep(self._poll_interval)
            return False
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
        except asyncio.TimeoutError:
            return False
        return True

    async def _poll_channel(self, team_id: str, channel_id: str, me_id: str) -> None:
        key = f"{team_id}/{channel_id}"
        channel_state = self._state["channels"][key]

        roots = await self._client.list_root_messages(team_id, channel_id, top=self._top_roots)
        replies_by_root: dict[str, list[dict[str, Any]]] = {}
        for root_id in selection.roots_to_poll(roots, channel_state):
            replies_by_root[root_id] = await self._client.list_replies(
                team_id, channel_id, root_id, top=self._top_replies
            )

        messages, new_channel_state = selection.plan_channel(
            roots,
            replies_by_root,
            channel_state,
            me_id=me_id,
            replied=set(self._state["replied"]),
            now=self._clock(),
            active_idle=self._active_idle,
        )

        for msg in messages:
            conversation_id = f"{team_id}/{channel_id}/{msg.thread_root_id}"
            # Materializuj załączniki (I/O) tuż przed obsługą — bajty trafiają na kopię
            # wiadomości, którą handler przekłada na treść multimodalną dla agenta.
            if self._materializer is not None and msg.attachment_refs:
                attachments = await self._materializer.materialize(team_id, channel_id, msg)
                msg = replace(msg, attachments=attachments)
            reply = await self._handle(msg, conversation_id)
            if reply:
                # Log bez TREŚCI (treść kanału to dane) — sam fakt odpowiedzi: obserwowalność.
                logger.info(
                    "Odpowiadam w %s/%s na wiadomość od %s (wątek %s)",
                    team_id,
                    channel_id,
                    msg.sender_name,
                    msg.thread_root_id,
                )
                await self._post_reply(team_id, channel_id, msg, reply)
            self._mark_replied(msg.id)

        # Watermark przesuwamy DOPIERO po obsłudze: gdy wysyłka zawiedzie PRZEJŚCIOWO (i
        # wyjdzie z pętli), nie zapisujemy postępu — następna runda ponowi, a dedup
        # ``replied`` pominie już odpisane. To daje semantykę „co najmniej raz" z dedup.
        self._state["channels"][key] = new_channel_state

    async def _post_reply(
        self, team_id: str, channel_id: str, msg: ChannelMessage, reply: str
    ) -> None:
        """Wyślij odpowiedź; TRWAŁY (4xx) błąd połknij, PRZEJŚCIOWY propaguj do ponowienia.

        Trwały błąd wysyłki (np. 403 bez zgody ``ChannelMessage.Send`` na kanale albo
        wiadomość nie do wysłania) NIE może zapętlić kanału — inaczej ta sama wiadomość
        wracałaby co rundę, blokując kolejne i wołając LLM w kółko. Połykamy go (wiadomość i
        tak zostanie oznaczona jako odpisana), a przejściowy (429/5xx/sieć) podnosimy —
        pętla wyżej nie przesunie watermarku i ponowi w następnej rundzie.
        """
        try:
            await self._client.post_reply(team_id, channel_id, msg.thread_root_id, reply)
        except Exception as exc:  # rozróżnienie po statusie HTTP, bez importu httpx tutaj
            if not _is_permanent(exc):
                raise
            logger.error(
                "Trwały błąd wysyłki (%s) w %s/%s — pomijam wiadomość %s",
                _status_of(exc),
                team_id,
                channel_id,
                msg.id,
            )

    def _mark_replied(self, msg_id: str) -> None:
        replied: list[str] = self._state["replied"]
        replied.append(msg_id)
        del replied[:-_REPLIED_CAP]  # zostaw ostatnie _REPLIED_CAP (no-op gdy krótsza)

    def _seed(self, startup_iso: str) -> None:
        """Zainicjuj brakujące gałęzie stanu (idempotentnie), nie ruszając zapisanych pozycji."""
        self._state.setdefault("replied", [])
        channels: dict[str, Any] = self._state.setdefault("channels", {})
        for team_id, channel_id in self._watch:
            channel = channels.setdefault(f"{team_id}/{channel_id}", {})
            channel.setdefault("since_roots", startup_iso)
            channel.setdefault("threads", {})
