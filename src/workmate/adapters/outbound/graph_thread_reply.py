"""Synchroniczny poster ODPOWIEDZI TEKSTOWEJ w wątku kanału Teams (B3 / ADR 0043).

Callback wyniku ``/notatka`` liczonego w tle: zadanie biegnie w puli wątków (sync), więc potrzebny
jest SYNCHRONICZNY poster — nie async ``TeamsNotifier`` pollera (którego nie da się wprost zawołać
z gołego wątku bez pętli). Ten sam token delegowany co drzwi ``teams_graph``, wołany wprost.

Wąski kontrakt: „wrzuć tekst do wątku ``team/channel/root``". 429 ponawiamy (żądanie odrzucone
przed przetworzeniem); 404 na root → ``ThreadRootGone`` (wątek usunięty) — wołający (zadanie w tle)
loguje i nie wywraca się. Treść idzie jako ``text`` (nie HTML): wynik notatki to zwykły tekst,
bez znaczników — brak ryzyka wstrzyknięcia znaczników.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from workmate.core.errors import ThreadRootGone

GRAPH = "https://graph.microsoft.com/v1.0"
_MAX_429_RETRIES = 5
_DEFAULT_RETRY_AFTER_S = 5


class HttpxGraphThreadReplyPoster:
    """``post`` wrzuca tekst do wątku ``team/channel/root`` (SYNC, delegowany token)."""

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

    def post(self, team_id: str, channel_id: str, root_id: str, text: str) -> None:
        """Wyślij odpowiedź tekstową w wątku; 404 na root → ``ThreadRootGone``."""
        # Nagłówek PER WYWOŁANIE, nie mutacja współdzielonego ``client.headers`` — poster bywa
        # wołany RÓWNOLEGLE z wielu wątków puli async (ADR 0043); mutacja stanu klienta byłaby
        # zapisem cross-thread. (Ten sam wzorzec co ``HttpxGraphTranscriptSource``.)
        headers = {"Authorization": f"Bearer {self._token()}"}
        url = f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages/{root_id}/replies"
        payload: dict[str, Any] = {"body": {"contentType": "text", "content": text}}
        for attempt in range(_MAX_429_RETRIES + 1):
            response = self._client.post(url, json=payload, headers=headers)
            if response.status_code == 429 and attempt < _MAX_429_RETRIES:
                # 429 = odrzucone przed przetworzeniem → powtórka bezpieczna (nie dubluje).
                self._sleep(_retry_after_s(response))
                continue
            if response.status_code == 404:
                raise ThreadRootGone(f"root wątku {root_id} nie istnieje")
            response.raise_for_status()
            return


def _retry_after_s(response: httpx.Response) -> float:
    """Sekundy do odczekania z nagłówka ``Retry-After`` (albo domyślne), odporne na śmieci."""
    raw = response.headers.get("Retry-After", "")
    try:
        return float(raw)
    except ValueError:
        return float(_DEFAULT_RETRY_AFTER_S)
