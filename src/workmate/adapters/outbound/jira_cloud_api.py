"""Klient Jira Cloud REST v3 (``httpx.Client`` + Basic ``email:api_token``) — port odczytu Jiry.

Bliźniak ``HttpxJiraClient`` (Server/DC), ale dla Jira Cloud (ADR 0033). Różnice wobec Server/DC:
auth **Basic** (email + API token, nie PAT Bearer); ścieżki ``/rest/api/3/...`` (nie v2); treść
``description``/``comment.body`` to **ADF** (spłaszczana do tekstu przy odczycie — NA GRANICY
adaptera, żeby wołający widział te same stringi co dla Server/DC); wyszukiwanie przez
**``POST /search/jql``** z paginacją kursorową (``nextPageToken``/``isLast``, bez ``total`` — stary
``/search`` jest na Cloud usunięty, 410 Gone).

**Ograniczenie (nota deprecacji Atlassian):** bulk ``/search/jql`` inline'uje changelog/komentarze,
ale UCINA każde do 20 pozycji — dla zapytań "moje zadania" (ADR 0054, bez ``expand``) to bez
znaczenia, bo changelog/komentarze nie są tu odczytywane. Transport idzie przez
``jira_http.request_with_retry`` — retry na 429/503.

Zapis (create/comment/tranzycja, ADR 0031/0032) i most push/ingest (ADR 0030) zostały USUNIĘTE —
ADR 0054 zredukował Jirę do jednej, wyłącznie odczytowej zdolności ("moje zadania").
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from workmate.adapters.outbound.jira_http import request_with_retry
from workmate.core.domain.adf import adf_to_text

logger = logging.getLogger(__name__)

# Cap stron na jedno pobranie — chroni przed nieograniczoną paginacją ORAZ przed znanym bugiem
# ``/search/jql`` (raporty o ``isLast`` nigdy=true i nieskończonym chainingu tokenów).
_MAX_PAGES = 10
# Pola dobierane w bulk-search — pokrywają "moje zadania" (ADR 0054): status/priorytet/termin do
# listy, ``project``/``created``/``updated``/``creator``/``reporter`` jako kontekst diagnostyczny.
_SEARCH_FIELDS = [
    "summary",
    "description",
    "status",
    "priority",
    "duedate",
    "created",
    "updated",
    "creator",
    "reporter",
    "project",
]


class HttpxJiraCloudClient:
    """Klient Jira Cloud REST v3 — Basic auth (email:api_token), ``search/jql``, treść jako ADF."""

    def __init__(self, client: httpx.Client, *, email: str, token: str, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        raw = f"{email}:{token}".encode()
        self._client.headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
        self._client.headers["Accept"] = "application/json"

    def authenticated_account(self) -> str:
        """Konto uwierzytelnione — na Cloud ``accountId`` (``name``/``key`` usunięte, RODO)."""
        data = self._get_json(f"{self._base_url}/rest/api/3/myself")
        if not isinstance(data, dict):
            return ""
        _warn_on_timezone_skew(data.get("timeZone"))
        return str(data.get("accountId") or data.get("name") or data.get("key") or "")

    def search_issues(
        self, jql: str, *, max_results: int = 50, expand: str = "changelog"
    ) -> list[dict[str, Any]]:
        """Bulk ``POST /search/jql`` z paginacją kursorową; ADF spłaszczony do tekstu przed zwrotem.

        Odpowiedź to ``{issues, nextPageToken?, isLast}`` (bez ``total``); pętla po kursorze do
        ``isLast``/braku tokenu, z twardym capem stron i obroną przed powtórzonym tokenem (znany
        bug paginacji Cloud).
        """
        issues: list[dict[str, Any]] = []
        seen_tokens: set[str] = set()
        next_token = ""
        for _ in range(_MAX_PAGES):
            payload: dict[str, Any] = {
                "jql": jql,
                "maxResults": max_results,
                "fields": _SEARCH_FIELDS,
            }
            if expand:
                payload["expand"] = expand
            if next_token:
                payload["nextPageToken"] = next_token
            body = self._post_json(f"{self._base_url}/rest/api/3/search/jql", payload)
            raw = body.get("issues")
            page = [i for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []
            for issue in page:
                _normalize_adf(issue)
                issues.append(issue)
            token = body.get("nextPageToken")
            next_token = str(token) if token else ""
            if body.get("isLast") or not next_token or not page or next_token in seen_tokens:
                break
            seen_tokens.add(next_token)
        return issues

    # --- transport ---------------------------------------------------------------

    def _get_json(self, url: str, params: dict[str, str] | None = None) -> Any:
        return request_with_retry(self._client, "GET", url, params=params).json()

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = request_with_retry(self._client, "POST", url, json=payload).json()
        return data if isinstance(data, dict) else {}


def _warn_on_timezone_skew(account_tz: Any) -> None:
    """Ostrzeż, gdy strefa konta usługowego różni się od strefy hosta.

    JQL bez strefy interpretuje daty w strefie ZALOGOWANEGO użytkownika, nie hosta — rozjazd może
    przesunąć interpretację dat granicznych w zapytaniach z warunkiem czasowym. Ostrzeżenie, nie
    błąd: rozjazd bywa świadomy (host w UTC).
    """
    name = str(account_tz or "").strip()
    if not name:
        return
    now = datetime.now(timezone.utc)
    try:
        account_offset = now.astimezone(ZoneInfo(name)).utcoffset()
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Strefa konta Jira %r jest nieznana — pomijam kontrolę rozjazdu.", name)
        return
    host_offset = now.astimezone().utcoffset()
    if account_offset != host_offset:
        logger.warning(
            "Strefa konta Jira (%s, offset %s) różni się od strefy hosta (offset %s) — daty "
            "graniczne w JQL mogą być interpretowane inaczej niż oczekiwano.",
            name,
            account_offset,
            host_offset,
        )


def _normalize_adf(issue: dict[str, Any]) -> None:
    """Spłaszcz ADF → tekst w miejscu: ``fields.description``.

    Dzięki temu mapowanie "moje zadania" (czyta ``description``/pola jako stringi) działa na
    Cloud bez zmian. ``None`` (brak opisu) zostaje ``None``.
    """
    fields = issue.get("fields")
    if not isinstance(fields, dict):
        return
    description = fields.get("description")
    if description is not None:
        fields["description"] = adf_to_text(description)
