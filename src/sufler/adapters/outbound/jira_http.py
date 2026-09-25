"""Transport HTTP wspólny dla klientów Jira (Server/DC i Cloud) — retry na throttling (A4).

Oba klienty (`jira_api.py`, `jira_cloud_api.py`) dotąd wołały `httpx.Client` wprost i przy 429
padały od razu jako `HTTPStatusError`, zamiast poczekać i ponowić jak `github_api.py`. Jedno
miejsce transportu, żeby nie duplikować logiki retry w dwóch adapterach (Server/DC i Cloud).
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from sufler.core.errors import JiraReadError

_MAX_RETRIES = 3
_DEFAULT_RETRY_AFTER_S = 5
# Sufit pojedynczego odczekania — nie blokujemy pollera na długo przy uporczywym throttlingu.
_MAX_BACKOFF_S = 60


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    params: dict[str, str] | None = None,
    json: dict[str, Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """Wykonaj żądanie; przy throttlingu odczekaj i ponów (do ``_MAX_RETRIES``), potem zwróć.

    Odczekanie honoruje ``Retry-After`` gdy Jira go poda; w jego braku wykładniczy backoff od
    ``_DEFAULT_RETRY_AFTER_S``, oba z sufitem ``_MAX_BACKOFF_S``. ``sleep`` wstrzykiwalny — testy
    podają no-op, żeby nie czekać naprawdę.
    """
    attempts = 0
    while True:
        response = client.request(method, url, params=params, json=json)
        if _is_retryable(response.status_code, method) and attempts < _MAX_RETRIES:
            attempts += 1
            sleep(_retry_wait(response, attempts))
            continue
        response.raise_for_status()
        return response


@contextlib.contextmanager
def as_jira_read_error() -> Iterator[None]:
    """Zamień błąd transportu na ``JiraReadError`` — TU, na granicy adaptera, nie w rdzeniu.

    Słownik sieci (``httpx``) kończy się na adapterze: rdzeń dostaje wyłącznie błąd domenowy
    z gotowym komunikatem dla pytającego. Wcześniej to samo tłumaczenie stało w warstwie
    aplikacji (``my_jira_tasks``/``jira_read``) i wciągało ``import httpx`` do heksagonu —
    czego ``lint-imports`` nie widzi, bo reguła zabrania tylko importów z ``sufler.adapters``.
    """
    try:
        yield
    except httpx.HTTPStatusError as exc:
        raise JiraReadError(_status_message(exc.response.status_code)) from exc
    except httpx.TimeoutException as exc:
        raise JiraReadError(
            "Jira nie odpowiedziała w wyznaczonym czasie (timeout) — spróbuj ponownie."
        ) from exc
    except httpx.HTTPError as exc:
        raise JiraReadError(f"nie udało się połączyć z Jirą: {exc}.") from exc


def _status_message(status_code: int) -> str:
    """Komunikat dla pytającego wg statusu HTTP — jedno brzmienie dla Server/DC i Cloud."""
    if status_code in (401, 403):
        return "brak dostępu do Jiry — token jest nieważny albo bez uprawnień odczytu."
    if status_code == 429:
        return "Jira ogranicza liczbę żądań (429) — spróbuj ponownie za chwilę."
    return f"Jira odpowiedziała błędem (HTTP {status_code})."


def _is_retryable(status_code: int, method: str) -> bool:
    """429 = throttling Jiry, bezpieczny na każdej metodzie (żądanie odrzucone przed obsługą).

    503 = przejściowa niedostępność (maintenance/przeciążenie) — retry TYLKO na GET. Na POST
    nieidempotentnym (``create_issue``/``add_comment``, bez klucza idempotencji) 503 może przyjść
    już PO utworzeniu zasobu (błąd bramy/LB przy zwrocie), więc ponowienie ryzykowałoby duplikat.
    """
    if status_code == 429:
        return True
    return status_code == 503 and method == "GET"


def _retry_wait(response: httpx.Response, attempt: int) -> float:
    """Sekundy odczekania: ``Retry-After`` albo wykładniczy backoff, oba przycięte do sufitu."""
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return max(0.0, min(float(retry_after), _MAX_BACKOFF_S))
        except ValueError:
            pass
    return min(_DEFAULT_RETRY_AFTER_S * (2 ** (attempt - 1)), _MAX_BACKOFF_S)
