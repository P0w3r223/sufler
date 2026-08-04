"""Klient Jira Server/DC REST v2 (``httpx.Client`` + PAT Bearer) — port odczytu Jiry.

Importowany LENIWIE (wymaga extra ``jira`` — ``httpx``). Sync (nie async): narzędzia wołają go
w puli wątków. PAT to SEKRET — wstrzykiwany, nigdy logowany. Paginacja po ``startAt`` (odpowiedź
``/search`` to KOPERTA ``{issues, total, startAt, maxResults}``, nie goła lista). Transport idzie
przez ``jira_http.request_with_retry`` — retry na 429/503.

Zapis (create/comment/tranzycja, ADR 0031/0032) i most push/ingest (ADR 0030) zostały USUNIĘTE —
ADR 0054 zredukował Jirę do jednej, wyłącznie odczytowej zdolności ("moje zadania").
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import httpx

from workmate.adapters.outbound.jira_http import request_with_retry
from workmate.core.errors import InvalidRequestError

if TYPE_CHECKING:
    from workmate.adapters.outbound.jira_cloud_api import HttpxJiraCloudClient
    from workmate.config import JiraSettings

# Cap stron na jedno pobranie — chroni przed nieograniczoną paginacją dużych projektów.
_MAX_PAGES = 10
# Kanoniczny klucz issue Jira (PROJEKT-NUMER) — walidacja przed wstawieniem do ścieżki URL.
_ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-\d+$")
# Pola dobierane dla szczegółów pojedynczego issue (parzystość z klientem Cloud).
_DETAIL_FIELDS = "summary,description,status,priority,duedate,created,updated,reporter,assignee"


class HttpxJiraClient:
    """Klient Jira REST v2 (Server/DC) oparty o ``httpx.Client`` i token PAT (Bearer)."""

    def __init__(self, client: httpx.Client, token: str, *, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._client.headers["Authorization"] = f"Bearer {token}"
        self._client.headers["Accept"] = "application/json"

    def authenticated_account(self) -> str:
        data = self._get_json(f"{self._base_url}/rest/api/2/myself")
        if isinstance(data, dict):
            return str(data.get("name") or data.get("key") or data.get("accountId") or "")
        return ""

    def search_issues(
        self, jql: str, *, max_results: int = 50, expand: str = "changelog"
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        start_at = 0
        for _ in range(_MAX_PAGES):
            body = self._get_json(
                f"{self._base_url}/rest/api/2/search",
                {
                    "jql": jql,
                    "startAt": str(start_at),
                    "maxResults": str(max_results),
                    "expand": expand,
                },
            )
            if not isinstance(body, dict):
                break
            raw = body.get("issues")
            page = [i for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []
            issues.extend(page)
            total = int(body.get("total") or 0)
            start_at += max_results
            if not page or start_at >= total:
                break
        return issues

    def get_issue(self, key: str) -> dict[str, Any]:
        """Jedno issue po kluczu (REST v2, Server/DC). Treść opisu to zwykły tekst (nie ADF)."""
        safe = _validate_key(key)
        data = self._get_json(
            f"{self._base_url}/rest/api/2/issue/{safe}", {"fields": _DETAIL_FIELDS}
        )
        return data if isinstance(data, dict) else {}

    def list_comments(self, key: str, *, max_results: int = 5) -> list[dict[str, Any]]:
        """Najnowsze komentarze issue (REST v2). Server/DC zwraca je rosnąco — bierzemy ogon."""
        safe = _validate_key(key)
        data = self._get_json(
            f"{self._base_url}/rest/api/2/issue/{safe}/comment",
            {"maxResults": str(max_results), "orderBy": "-created"},
        )
        raw = data.get("comments") if isinstance(data, dict) else None
        comments = [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []
        return comments[-max_results:] if len(comments) > max_results else comments

    # --- transport ---------------------------------------------------------------

    def _get_json(self, url: str, params: dict[str, str] | None = None) -> Any:
        return request_with_retry(self._client, "GET", url, params=params).json()


def _validate_key(key: str) -> str:
    """Zwaliduj klucz issue przed wstawieniem do ścieżki URL (ochrona przed traversalem)."""
    safe = key.strip()
    if not _ISSUE_KEY_RE.match(safe):
        raise InvalidRequestError(
            f"Niepoprawny klucz zgłoszenia {key!r} — oczekuję postaci 'WT-5'."
        )
    return safe


def build_jira_client(
    sync_http: httpx.Client, settings: JiraSettings
) -> HttpxJiraClient | HttpxJiraCloudClient:
    """Fabryka klienta Jira wg ``settings.deployment`` (ADR 0033) — JEDNO źródło wyboru providera.

    ``cloud`` → ``HttpxJiraCloudClient`` (Basic ``email:api_token``, REST v3, ``search/jql``);
    inaczej ``HttpxJiraClient`` (Server/DC — PAT Bearer, REST v2). Oba spełniają ``JiraReadPort``,
    więc wołający ich nie rozróżnia. Klient Cloud ładowany LENIWIE (gdy trzeba).
    """
    if settings.deployment.strip().lower() == "cloud":
        from workmate.adapters.outbound.jira_cloud_api import HttpxJiraCloudClient

        return HttpxJiraCloudClient(
            sync_http, email=settings.email, token=settings.token, base_url=settings.base_url
        )
    return HttpxJiraClient(sync_http, settings.token, base_url=settings.base_url)
