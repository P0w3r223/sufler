"""Klient Jira Server/DC REST v2 (``httpx.Client`` + PAT Bearer) — porty read/write Jira.

Importowany LENIWIE (wymaga extra ``jira`` — ``httpx``). Sync (nie async): poller/narzędzia wołają
w puli wątków (jak ``github_api``). PAT to SEKRET — wstrzykiwany, nigdy logowany. Paginacja po
``startAt`` (odpowiedź ``/search`` to KOPERTA ``{issues, total, startAt, maxResults}``, nie goła
lista). Zapis (Gate 5 / ADR 0031) jest CREATE-ONLY i tłumaczy błąd HTTP na domenowy ``WriteError``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import httpx

from workmate.core.errors import WriteError

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

    # --- write (ADR 0031, bramkowane) — CREATE-ONLY -----------------------------

    def create_issue(
        self,
        project: str,
        issue_type: str,
        summary: str,
        description: str,
        labels: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "project": {"key": project},
            "issuetype": {"name": issue_type},
            "summary": summary,
            "description": description,
        }
        if labels:
            fields["labels"] = list(labels)
        with _as_write_error("utworzyć zgłoszenia"):
            created = self._post_json(f"{self._base_url}/rest/api/2/issue", {"fields": fields})
        key = str(created.get("key") or "")
        return {"key": key, "url": self._browse(key), "created": self._fetch_created(key)}

    def add_comment(self, issue_key: str, body: str) -> dict[str, Any]:
        with _as_write_error("dodać komentarza"):
            created = self._post_json(
                f"{self._base_url}/rest/api/2/issue/{issue_key}/comment", {"body": body}
            )
        comment_id = str(created.get("id") or "")
        return {
            "id": comment_id,
            "url": self._browse(issue_key, comment_id=comment_id),
            "created": str(created.get("created") or ""),
        }

    def _fetch_created(self, key: str) -> str:
        """Znacznik ``created`` zgłoszenia osobnym GET-em (odpowiedź create Jiry go nie niesie).

        Best-effort: potrzebny tylko do ostemplowania echa ``source="teams"``; błąd GET-a (issue już
        powstało) → pusty znacznik, serwis pominie echo. Dlatego POZA ``_as_write_error`` — nie
        zamieniamy nieudanego dobrania daty na błąd zapisu, który się PODAŁ.
        """
        if not key:
            return ""
        try:
            data = self._get_json(f"{self._base_url}/rest/api/2/issue/{key}", {"fields": "created"})
        except httpx.HTTPError:
            return ""
        fields = data.get("fields") if isinstance(data, dict) else None
        return str(fields.get("created") or "") if isinstance(fields, dict) else ""

    def _browse(self, key: str, *, comment_id: str = "") -> str:
        if not key:
            return ""
        url = f"{self._base_url}/browse/{key}"
        return f"{url}?focusedCommentId={comment_id}" if comment_id else url

    # --- transport ---------------------------------------------------------------

    def _get_json(self, url: str, params: dict[str, str] | None = None) -> Any:
        response = self._client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}


@contextlib.contextmanager
def _as_write_error(action: str) -> Iterator[None]:
    """Zamień błąd HTTP zapisu na ``WriteError`` — granica: narzędzie zwróci ``{"error": ...}``.

    Zapis do Jiry (issue/komentarz) idzie przez narzędzie agenta, którego koperta łapie
    ``WorkMateError``. Surowy ``httpx.HTTPError`` (np. 400 walidacji pól, 403 uprawnień) mapujemy tu
    na domenowy ``WriteError``, żeby model dostał czytelny błąd zamiast wywrócenia tury.
    """
    try:
        yield
    except httpx.HTTPStatusError as exc:
        raise WriteError(f"nie udało się {action} (HTTP {exc.response.status_code}).") from exc
    except httpx.HTTPError as exc:
        raise WriteError(f"nie udało się {action}: {exc}.") from exc
