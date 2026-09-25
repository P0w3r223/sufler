"""Synchroniczne źródło treści WĄTKU kanału Teams z Microsoft Graph (ADR 0048).

Implementuje port ``ThreadSource`` dla przechwycenia „zapisz to": pobiera post-root i jego
odpowiedzi, składa je w materiał źródłowy dla summarizera (``Nazwa: treść``) i zbiera REALNYCH
nadawców jako roster uczestników (z metadanych Graph, nie z LLM — anty-halucynacja, ADR 0047).

SYNCHRONICZNE (``httpx.Client``), bo ``ThreadNoteService`` biegnie w wątku puli (jak
``HttpxGraphTranscriptSource``/``HttpxGraphThreadReplyPoster``), nie w pętli async pollera. Ten
sam delegowany token co drzwi ``teams_graph``, nagłówek PER WYWOŁANIE (poster/źródło bywają
wołane równolegle — mutacja ``client.headers`` byłaby zapisem cross-thread). 429 ponawiamy
(odczyt idempotentny); 404 na root → ``ThreadRootGone`` (wątek usunięty).

Treść wątku to DANE, nie polecenia (zasada przekrojowa) — tu tylko ją pobieramy i formatujemy.
"""

from __future__ import annotations

import html
import logging
import re
import time
from collections.abc import Callable
from typing import Any

import httpx

from sufler.core.errors import ThreadRootGone
from sufler.core.ports.thread import ThreadContent

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
_MAX_429_RETRIES = 5
_DEFAULT_RETRY_AFTER_S = 5
# Sufit stron ``/replies`` — długi wątek nie ma puchnąć w nieskończoność (koszt API + kontekst).
_MAX_REPLY_PAGES = 10
_TOP_REPLIES = 50


class HttpxGraphThreadSource:
    """``fetch`` pobiera wątek ``team/channel/root`` i zwraca treść + realnych nadawców (SYNC)."""

    def __init__(
        self,
        client: httpx.Client,
        token_provider: Callable[[], str],
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._token = token_provider
        self._sleep = sleep  # wstrzykiwalny, by testy nie odczekiwały realnego backoffu

    def fetch(self, external_id: str) -> ThreadContent:
        """Zwróć treść wątku (root + odpowiedzi) i nadawców; 404 na root → ``ThreadRootGone``.

        ``external_id`` = ``team/channel/root`` (jak cel odpowiedzi). Zły format → ``ValueError``
        (degraduje w routerze do czytelnego komunikatu). Wiadomości nie-treści (zdarzenia systemowe,
        skasowane, puste) pomijamy — nie są materiałem notatki.
        """
        team_id, channel_id, root_id = _split_external_id(external_id)
        base = f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages/{root_id}"
        try:
            root = self._get(base)
            replies = self._get_all(f"{base}/replies", params={"$top": str(_TOP_REPLIES)})
        except httpx.HTTPStatusError as exc:
            # 404 (root usunięty) już dało ``ThreadRootGone`` w ``_get`` przed raise_for_status.
            # Inne nie-2xx (403 brak zakresu odczytu kanału, 401 wygasły token, 5xx awaria) mapujemy
            # na ``ValueError`` z podpowiedzią — inaczej surowy HTTPStatusError omija degradację
            # routera i ląduje jako defekt kodu (lustro ``HttpxGraphTranscriptSource``).
            raise ValueError(
                f"Graph odrzucił pobranie wątku (HTTP {exc.response.status_code}) — sprawdź zakres "
                "odczytu kanału (ChannelMessage.Read.All) i uprawnienia do zespołu."
            ) from exc
        # Chronologicznie: root najpierw, potem odpowiedzi wg czasu utworzenia (endpoint bez sortu).
        ordered = [root, *sorted(replies, key=lambda m: m.get("createdDateTime") or "")]
        lines: list[str] = []
        participants: list[str] = []
        seen: set[str] = set()
        for raw in ordered:
            if raw.get("messageType") != "message" or raw.get("deletedDateTime"):
                continue
            text = _text_from_html((raw.get("body") or {}).get("content"))
            if not text:
                continue
            name = ((raw.get("from") or {}).get("user") or {}).get("displayName") or "?"
            lines.append(f"{name}: {text}")
            if name != "?" and name not in seen:
                seen.add(name)
                participants.append(name)
        return ThreadContent(text="\n".join(lines), participants=tuple(participants))

    def _get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token()}"}
        for attempt in range(_MAX_429_RETRIES + 1):
            response = self._client.get(url, params=params, headers=headers)
            if response.status_code == 429 and attempt < _MAX_429_RETRIES:
                self._sleep(_retry_after_s(response))
                continue
            if response.status_code == 404:
                raise ThreadRootGone(f"wątek {url} nie istnieje")
            response.raise_for_status()
            return dict(response.json())
        raise RuntimeError("wyczerpano ponowienia 429 przy pobraniu wątku")  # pragma: no cover

    def _get_all(self, url: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url: str | None = url
        pages = 0
        while next_url and pages < _MAX_REPLY_PAGES:
            data = self._get(next_url, params=params)
            items.extend(data.get("value", []))
            next_url = data.get("@odata.nextLink")
            params = None  # nextLink niesie już parametry
            pages += 1
        if next_url:
            # Świadome ucięcie długiego wątku (sufit stron): notatka = streszczenie, więc ogon
            # pomijamy, ale NIE po cichu — log, by wiadome było, że materiał jest niekompletny.
            logger.info(
                "Wątek dłuższy niż %d stron odpowiedzi — notatka streszcza pierwsze %d.",
                _MAX_REPLY_PAGES,
                len(items),
            )
        return items


def _split_external_id(external_id: str) -> tuple[str, str, str]:
    """Rozbij ``team/channel/root`` na trójkę; inny kształt → ``ValueError`` (nie ciche puste)."""
    parts = external_id.split("/")
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"zły external_id wątku (oczekiwano team/channel/root): {external_id!r}")
    return parts[0], parts[1], parts[2]


def _text_from_html(raw: str | None) -> str:
    """Usuń znaczniki HTML i odkoduj encje — treść wiadomości kanału bywa w HTML (jak selection)."""
    return html.unescape(re.sub(r"<[^>]+>", "", raw or "")).strip()


def _retry_after_s(response: httpx.Response) -> float:
    """Sekundy do odczekania z nagłówka ``Retry-After`` (albo domyślne), odporne na śmieci."""
    raw = response.headers.get("Retry-After", "")
    try:
        return float(raw)
    except ValueError:
        return float(_DEFAULT_RETRY_AFTER_S)
