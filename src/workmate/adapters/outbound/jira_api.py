"""Klient Jira Server/DC REST v2 (``httpx.Client`` + PAT Bearer) — port odczytu Jiry.

Importowany LENIWIE (wymaga extra ``jira`` — ``httpx``). Sync (nie async): narzędzia wołają go
w puli wątków. PAT to SEKRET — wstrzykiwany, nigdy logowany. Paginacja po ``startAt`` (odpowiedź
``/search`` to KOPERTA ``{issues, total, startAt, maxResults}``, nie goła lista). Transport idzie
przez ``jira_http.request_with_retry`` — retry na 429/503.

Zapis (create/comment/tranzycja, ADR 0031/0032) i most push/ingest (ADR 0030) zostały USUNIĘTE —
ADR 0054 zredukował Jirę do jednej, wyłącznie odczytowej zdolności ("moje zadania").
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from workmate.adapters.outbound.jira_http import request_with_retry

if TYPE_CHECKING:
    from workmate.adapters.outbound.jira_cloud_api import HttpxJiraCloudClient
    from workmate.config import JiraSettings

# Cap stron na jedno pobranie — chroni przed nieograniczoną paginacją dużych projektów.
_MAX_PAGES = 10


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

    # --- transport ---------------------------------------------------------------

    def _get_json(self, url: str, params: dict[str, str] | None = None) -> Any:
        return request_with_retry(self._client, "GET", url, params=params).json()


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
