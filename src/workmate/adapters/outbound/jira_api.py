"""Klient Jira Server/DC REST v2 (``httpx.Client`` + PAT Bearer) — implementacja ``JiraReadPort``.

Importowany LENIWIE (wymaga extra ``jira`` — ``httpx``). Sync (nie async): poller woła go w puli
wątków (jak ``github_api``). PAT to SEKRET — wstrzykiwany, nigdy logowany. Paginacja po ``startAt``
(odpowiedź ``/search`` to KOPERTA ``{issues, total, startAt, maxResults}``, nie goła lista).
"""

from __future__ import annotations

from typing import Any

import httpx

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

    def _get_json(self, url: str, params: dict[str, str] | None = None) -> Any:
        response = self._client.get(url, params=params)
        response.raise_for_status()
        return response.json()
