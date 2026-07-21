"""Klient GitHub REST (``httpx.Client`` + PAT) — implementacja portów read/write GitHub.

Importowany LENIWIE (w ``app.py``/wiringu), bo wymaga extra ``github`` (``httpx``). Sync
(nie async): poller woła go w puli wątków (jak teams_graph sync MSAL), a narzędzia agenta
wprost. Obsługuje limit zapytań GitHub (403/429 z ``X-RateLimit-Reset``/``Retry-After``) i
paginację po nagłówku ``Link`` (``rel="next"``). PAT to SEKRET — wstrzykiwany, nigdy logowany.
"""

from __future__ import annotations

import contextlib
import re
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import httpx

from workmate.core.errors import WriteError
from workmate.core.ports.github import MAX_COMMITS_PER_FETCH

_API_VERSION = "2022-11-28"
# Cap stron na jedno pobranie — chroni przed nieskończoną paginacją i wypaleniem limitu.
_MAX_PAGES = 10
# Największa strona, jaką przyjmuje GitHub REST. Prośba o więcej jest po cichu przycinana,
# więc przycinamy sami — inaczej budżet stron liczony z ``per_page`` byłby zawyżony.
_MAX_PER_PAGE = 100
_MAX_RETRIES = 3
_DEFAULT_RETRY_AFTER_S = 5
# Sufit pojedynczego odczekania na reset limitu — nie blokujemy pollera na godziny.
_MAX_BACKOFF_S = 60
_NEXT_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


class HttpxGithubClient:
    """Konkretny klient GitHub oparty o ``httpx.Client`` i token PAT (read + write)."""

    def __init__(
        self, client: httpx.Client, token: str, *, api_base: str = "https://api.github.com"
    ) -> None:
        self._client = client
        self._api_base = api_base.rstrip("/")
        self._client.headers["Authorization"] = f"Bearer {token}"
        self._client.headers["Accept"] = "application/vnd.github+json"
        self._client.headers["X-GitHub-Api-Version"] = _API_VERSION

    # --- read (ADR 0020) ---------------------------------------------------------

    def authenticated_login(self) -> str:
        data = self._get_json(f"{self._api_base}/user")
        return str(data.get("login", "")) if isinstance(data, dict) else ""

    def list_issues(
        self, owner: str, repo: str, *, since: datetime | None = None, per_page: int = 50
    ) -> list[dict[str, Any]]:
        params = {
            "state": "all",
            "sort": "updated",
            "direction": "asc",
            "per_page": str(per_page),
        }
        if since is not None:
            params["since"] = _iso_z(since)
        return self._get_all(f"{self._api_base}/repos/{owner}/{repo}/issues", params)

    def list_issue_comments(
        self, owner: str, repo: str, *, since: datetime | None = None, per_page: int = 50
    ) -> list[dict[str, Any]]:
        params = {"sort": "updated", "direction": "asc", "per_page": str(per_page)}
        if since is not None:
            params["since"] = _iso_z(since)
        return self._get_all(f"{self._api_base}/repos/{owner}/{repo}/issues/comments", params)

    def list_pull_reviews(self, owner: str, repo: str, pull_number: int) -> list[dict[str, Any]]:
        # Endpoint recenzji jest per-PR i bez ``since`` — poller ogranicza liczbę PR-ów/rundę,
        # a watermark ``reviews_since`` (w selection) odsiewa już widziane; dedup magazynu domyka.
        return self._get_all(
            f"{self._api_base}/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
            {"per_page": "100"},
        )

    def list_pulls(
        self, owner: str, repo: str, *, state: str = "all", per_page: int = 50
    ) -> list[dict[str, Any]]:
        # Dedykowany /pulls (bogatszy niż z /issues): ``merged_at``, ``draft``, ``head``/``base``,
        # ``state`` — snapshot stanu PR i tranzycji (ADR 0029). Sort desc po aktualizacji.
        return self._get_all(
            f"{self._api_base}/repos/{owner}/{repo}/pulls",
            {"state": state, "sort": "updated", "direction": "desc", "per_page": str(per_page)},
        )

    def list_branches(self, owner: str, repo: str, *, per_page: int = 50) -> list[dict[str, Any]]:
        # Gałęzie repo: nazwa + HEAD SHA (``commit.sha``) — snapshot i wykrycie pushy (ADR 0029).
        return self._get_all(
            f"{self._api_base}/repos/{owner}/{repo}/branches",
            {"per_page": str(per_page)},
        )

    def list_commits(
        self,
        owner: str,
        repo: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        author: str = "",
        per_page: int = 50,
    ) -> list[dict[str, Any]]:
        # Commity GAŁĘZI DOMYŚLNEJ w oknie czasu (ADR 0034). ``author`` zawęża do jednej osoby
        # — GitHub dopasowuje go do loginu ALBO adresu e-mail autora commita, więc inny
        # ``git config user.email`` da cichy zerowy wynik (propozycja ostrzega o tym w ``notes``).
        # Bez diffów i patchy.
        #
        # Budżet stron liczymy Z SUFITU KONTRAKTU, nie z ogólnego ``_MAX_PAGES``. Rdzeń rozpoznaje
        # ucięcie po DOKŁADNEJ liczbie pozycji (pełne wiadro = zgubione najstarsze dni), więc sufit
        # musi być OSIĄGALNY niezależnie od rozmiaru strony. Przy wspólnym ``_MAX_PAGES`` mniejsze
        # ``per_page`` dawałoby mniej niż ``MAX_COMMITS_PER_FETCH`` — ucięcie znowu byłoby CICHE,
        # czyli dokładnie ten defekt, który sufit miał zlikwidować.
        size = max(1, min(per_page, _MAX_PER_PAGE))
        params = {"per_page": str(size)}
        if since is not None:
            params["since"] = _iso_z(since)
        if until is not None:
            params["until"] = _iso_z(until)
        if author:
            params["author"] = author
        return self._get_all(
            f"{self._api_base}/repos/{owner}/{repo}/commits",
            params,
            limit=MAX_COMMITS_PER_FETCH,
            max_pages=-(-MAX_COMMITS_PER_FETCH // size),
        )

    def list_workflow_runs(
        self, owner: str, repo: str, *, per_page: int = 50, status: str = "completed"
    ) -> list[dict[str, Any]]:
        # Odpowiedź to KOPERTA ``{"total_count", "workflow_runs": [...]}`` (nie goła lista) — więc
        # nie przez ``_get_all``; bierzemy PIERWSZĄ STRONĘ zakończonych przebiegów (sort desc wg
        # UTWORZENIA). Watermark ``runs_since`` (na ``updated_at``) odsiewa już widziane.
        # OGRANICZENIE (ADR 0024, do rewizji przy dużym wolumenie CI): re-run zachowuje stare
        # ``created_at``, więc gdy między nim a rundą powstanie >``per_page`` nowszych przebiegów,
        # wypadnie poza pierwszą stronę i zostanie pominięty. Dla pilotażu (niski wolumen) OK.
        params = {"status": status, "per_page": str(per_page)}
        body = self._get_json(f"{self._api_base}/repos/{owner}/{repo}/actions/runs", params)
        if isinstance(body, dict):
            runs = body.get("workflow_runs")
            if isinstance(runs, list):
                return [run for run in runs if isinstance(run, dict)]
        return []

    # --- write (ADR 0021, bramkowane) --------------------------------------------

    def create_issue(
        self, owner: str, repo: str, title: str, body: str, labels: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = list(labels)
        with _as_write_error("utworzyć issue"):
            return self._post_json(f"{self._api_base}/repos/{owner}/{repo}/issues", payload)

    def create_comment(self, owner: str, repo: str, issue_number: int, body: str) -> dict[str, Any]:
        with _as_write_error("dodać komentarza"):
            return self._post_json(
                f"{self._api_base}/repos/{owner}/{repo}/issues/{issue_number}/comments",
                {"body": body},
            )

    # --- transport ---------------------------------------------------------------

    def _get_json(self, url: str, params: dict[str, str] | None = None) -> Any:
        return self._request("GET", url, params=params).json()

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._request("POST", url, json=payload).json()
        return data if isinstance(data, dict) else {}

    def _get_all(
        self,
        url: str,
        params: dict[str, str],
        *,
        limit: int | None = None,
        max_pages: int = _MAX_PAGES,
    ) -> list[dict[str, Any]]:
        """Zbierz pozycje ze wszystkich stron; ``limit`` docina wynik do sufitu kontraktu portu.

        Domyślnie bound stanowi sam ``max_pages``, więc realny sufit zależy od ``per_page``
        wołającego. Wołający, który obiecuje sufit W POZYCJACH (``list_commits``), podaje OBA:
        ``limit`` i pasujący do niego budżet stron — inaczej dwie niezależne granice rozjeżdżają
        się przy zmianie którejkolwiek stałej.
        """
        items: list[dict[str, Any]] = []
        pages = 0
        next_url: str | None = url
        next_params: dict[str, str] | None = params
        while next_url and pages < max_pages:
            response = self._request("GET", next_url, params=next_params)
            body = response.json()
            if isinstance(body, list):
                items.extend(x for x in body if isinstance(x, dict))
            if limit is not None and len(items) >= limit:
                return items[:limit]
            next_url = _next_link(response.headers.get("Link"))
            next_params = None  # nagłówek Link niesie już parametry
            pages += 1
        return items

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        attempts = 0
        while True:
            response = self._client.request(method, url, params=params, json=json)
            if _is_rate_limited(response) and attempts < _MAX_RETRIES:
                attempts += 1
                time.sleep(_rate_limit_wait(response))
                continue
            response.raise_for_status()
            return response


@contextlib.contextmanager
def _as_write_error(action: str) -> Iterator[None]:
    """Zamień błąd HTTP zapisu na ``WriteError`` — granica: narzędzie zwróci ``{"error": ...}``.

    Zapis do GitHub (issue/komentarz) idzie przez narzędzie agenta, którego koperta łapie
    ``WorkMateError``. Surowy ``httpx.HTTPError`` (np. 422 walidacji, 403 uprawnień) tłumaczymy
    tu na domenowy ``WriteError``, żeby model dostał czytelny błąd zamiast wywrócenia tury.
    """
    try:
        yield
    except httpx.HTTPStatusError as exc:
        raise WriteError(f"nie udało się {action} (HTTP {exc.response.status_code}).") from exc
    except httpx.HTTPError as exc:
        raise WriteError(f"nie udało się {action}: {exc}.") from exc


def _iso_z(when: datetime) -> str:
    """Znacznik ``since`` GitHuba — ISO 8601 UTC z ``Z`` (naive traktujemy jak UTC)."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_link(link_header: str | None) -> str | None:
    """URL następnej strony z nagłówka ``Link`` (``rel="next"``) albo ``None``."""
    if not link_header:
        return None
    match = _NEXT_LINK_RE.search(link_header)
    return match.group(1) if match else None


def _is_rate_limited(response: httpx.Response) -> bool:
    """Czy odpowiedź to wyczerpany limit zapytań (403/429 z sygnałem reset/retry)."""
    if response.status_code not in (403, 429):
        return False
    return response.headers.get("X-RateLimit-Remaining") == "0" or "Retry-After" in response.headers


def _rate_limit_wait(response: httpx.Response) -> float:
    """Sekundy odczekania: Retry-After albo do X-RateLimit-Reset (z górnym sufitem backoffu)."""
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return min(float(retry_after), _MAX_BACKOFF_S)
        except ValueError:
            return _DEFAULT_RETRY_AFTER_S
    reset = response.headers.get("X-RateLimit-Reset")
    if reset is not None:
        try:
            return max(0.0, min(float(reset) - time.time(), _MAX_BACKOFF_S))
        except ValueError:
            return _DEFAULT_RETRY_AFTER_S
    return _DEFAULT_RETRY_AFTER_S
