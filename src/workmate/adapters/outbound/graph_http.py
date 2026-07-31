"""Transport HTTP wspólny dla klientów Microsoft Graph — retry na throttling i błędy przejściowe.

Cztery adaptery (``graph_file_sender``, ``graph_user_doc_push``, ``graph_user_push`` — sync;
``graph_teams_notifier`` — async) miały tę samą politykę ponawiania skopiowaną osobno (dwie
pierwsze identyczne co do bajtu poza docstringiem) — dokładnie ten rodzaj dryfu, który
``jira_http.request_with_retry`` już raz naprawił dla klientów Jiry. Jedno miejsce, dwa warianty
(sync/async, bo ``httpx.Client``/``httpx.AsyncClient`` i pula wątków vs jedna pętla asyncio tego
wymagają), nad tą samą logiką decyzji.

**429 jest bezpieczny bez wyjątku**: limit żądań znaczy, że Graph ODRZUCIŁ żądanie przed
przetworzeniem, i mówi wprost, ile czekać (``Retry-After``) — stąd hojne pięć prób, zawsze.

**5xx i błąd transportu (timeout, zerwane połączenie) są bezpieczne TYLKO dla żądań
idempotentnych** — timeout znaczy „nie wiadomo, czy usługa przyjęła"; jeśli przyjęła, a
odpowiedź zginęła, powtórzenie żądania, które coś TWORZY (wiadomość, załącznik), wysyła je
DRUGI RAZ. Dlatego ``retry_transient`` jest opt-in per wywołanie — każdy adapter włącza je
wyłącznie dla GET-ów i operacji, które Graph sam czyni idempotentnymi (np. tworzenie czatu 1:1
zwraca istniejący czat), nigdy dla wysyłki wiadomości/pliku/obrazu.

Wariant ``async_request_with_retry`` woła ``asyncio.sleep`` przez ATRYBUT modułu (nie importem
``from asyncio import sleep``), żeby istniejące testy monkeypatchujące
``<moduł adaptera>.asyncio.sleep`` (ten sam obiekt modułu ``asyncio`` w całym procesie) nadal
przechwytywały odczekiwanie — bez tego przeniesienie pętli tutaj po cichu zepsułoby te testy.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import httpx

_MAX_429_RETRIES = 5
_DEFAULT_RETRY_AFTER_S = 5
_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})
_MAX_TRANSIENT_RETRIES = 3
_TRANSIENT_BACKOFF_S = 2


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    json: dict[str, Any] | None = None,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
    retry_transient: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """Wykonaj żądanie (sync); ponów 429 ZAWSZE, a 5xx/timeout tylko gdy to bezpieczne.

    ``sleep`` wstrzykiwalny (wołający przekazuje własny, np. ``self._sleep`` adaptera) — testy
    podają no-op, żeby nie czekać naprawdę.
    """
    throttled = 0
    transient = 0
    while True:
        try:
            response = client.request(method, url, json=json, content=content, headers=headers)
        except httpx.TransportError:
            if not retry_transient or transient >= _MAX_TRANSIENT_RETRIES:
                raise
            transient += 1
            sleep(_TRANSIENT_BACKOFF_S * transient)
            continue
        if response.status_code == 429 and throttled < _MAX_429_RETRIES:
            throttled += 1
            sleep(_retry_after(response))
            continue
        if (
            retry_transient
            and response.status_code in _RETRYABLE_STATUS
            and transient < _MAX_TRANSIENT_RETRIES
        ):
            transient += 1
            sleep(_TRANSIENT_BACKOFF_S * transient)
            continue
        response.raise_for_status()
        return response


async def async_request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    json: dict[str, Any] | None = None,
    retry_transient: bool = False,
) -> httpx.Response:
    """Wariant async — lustro ``request_with_retry`` nad tą samą logiką decyzji.

    Odczekiwanie idzie przez ``asyncio.sleep`` (atrybut modułu, patrz docstring modułu) — celowo
    BEZ wstrzykiwanego parametru ``sleep``, żeby testy mogły dalej monkeypatchować
    ``asyncio.sleep`` bezpośrednio, tak jak przed przeniesieniem tej pętli tutaj.
    """
    throttled = 0
    transient = 0
    while True:
        try:
            response = await client.request(method, url, json=json)
        except httpx.TransportError:
            if not retry_transient or transient >= _MAX_TRANSIENT_RETRIES:
                raise
            transient += 1
            await asyncio.sleep(_TRANSIENT_BACKOFF_S * transient)
            continue
        if response.status_code == 429 and throttled < _MAX_429_RETRIES:
            throttled += 1
            await asyncio.sleep(_retry_after(response))
            continue
        if (
            retry_transient
            and response.status_code in _RETRYABLE_STATUS
            and transient < _MAX_TRANSIENT_RETRIES
        ):
            transient += 1
            await asyncio.sleep(_TRANSIENT_BACKOFF_S * transient)
            continue
        response.raise_for_status()
        return response


def _retry_after(response: httpx.Response) -> int:
    """Sekundy odczekania z nagłówka Retry-After (fallback, gdy brak/niepoprawny)."""
    try:
        return int(response.headers.get("Retry-After", _DEFAULT_RETRY_AFTER_S))
    except ValueError:
        return _DEFAULT_RETRY_AFTER_S
