"""Klient Jira Cloud REST v3 (``httpx.Client`` + Basic ``email:api_token``) — porty read/write Jira.

Bliźniak ``HttpxJiraClient`` (Server/DC), ale dla Jira Cloud (ADR 0033). Różnice wobec Server/DC:
auth **Basic** (email + API token, nie PAT Bearer); ścieżki ``/rest/api/3/...`` (nie v2); treść
``description``/``comment.body`` to **ADF** (kodowana przy zapisie, spłaszczana do tekstu przy
odczycie — NA GRANICY adaptera, żeby ``selection``/poller/serwisy widziały te same stringi co dla
Server/DC); wyszukiwanie przez **``POST /search/jql``** z paginacją kursorową (``nextPageToken``/
``isLast``, bez ``total`` — stary ``/search`` jest na Cloud usunięty, 410 Gone).

**Ograniczenie (nota deprecacji Atlassian):** bulk ``/search/jql`` inline'uje changelog/komentarze,
ale UCINA każde do 20 pozycji. Dla pollera inkrementalnego (krótkie okno od watermarku, świeże)
to praktycznie zawsze wystarcza; przy >20 zmianach/komentarzach jednego zgłoszenia MIĘDZY pollami
część historii może zostać pominięta — świadome ograniczenie pilotażu (fallback per-issue: osobny
``GET /issue/{key}/changelog`` i ``/comment`` — patrz ADR 0033, TODO). Zapis (Gate 5 / ADR 0031) to
CREATE-ONLY i tłumaczy błąd HTTP na domenowy ``WriteError`` (jak Server/DC).
"""

from __future__ import annotations

import base64
import contextlib
from collections.abc import Iterator
from typing import Any

import httpx

from workmate.core.domain.adf import adf_to_text, text_to_adf
from workmate.core.errors import WriteError

# Cap stron na jedno pobranie — chroni przed nieograniczoną paginacją ORAZ przed znanym bugiem
# ``/search/jql`` (raporty o ``isLast`` nigdy=true i nieskończonym chainingu tokenów).
_MAX_PAGES = 10
# Pola dobierane w bulk-search — pokrywają to, co czyta ``jira.selection`` (utworzenie/tranzycja/
# komentarz). ``comment`` daje komentarze inline; ``expand=changelog`` daje historię statusów.
_SEARCH_FIELDS = [
    "summary",
    "description",
    "status",
    "created",
    "updated",
    "creator",
    "reporter",
    "comment",
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
        if isinstance(data, dict):
            return str(data.get("accountId") or data.get("name") or data.get("key") or "")
        return ""

    def search_issues(
        self, jql: str, *, max_results: int = 50, expand: str = "changelog"
    ) -> list[dict[str, Any]]:
        """Bulk ``POST /search/jql`` z paginacją kursorową; ADF spłaszczony do tekstu przed zwrotem.

        Odpowiedź to ``{issues, nextPageToken?, isLast}`` (bez ``total``); pętla po kursorze do
        ``isLast``/braku tokenu, z twardym capem stron i obroną przed powtórzonym tokenem (znany bug
        paginacji Cloud). Changelog i komentarze inline (cap 20/20 — patrz docstring modułu).
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
            "description": text_to_adf(description),
        }
        if labels:
            fields["labels"] = list(labels)
        with _as_write_error("utworzyć zgłoszenia"):
            created = self._post_json(f"{self._base_url}/rest/api/3/issue", {"fields": fields})
        key = str(created.get("key") or "")
        return {"key": key, "url": self._browse(key), "created": self._fetch_created(key)}

    def add_comment(self, issue_key: str, body: str) -> dict[str, Any]:
        with _as_write_error("dodać komentarza"):
            created = self._post_json(
                f"{self._base_url}/rest/api/3/issue/{issue_key}/comment",
                {"body": text_to_adf(body)},
            )
        comment_id = str(created.get("id") or "")
        return {
            "id": comment_id,
            "url": self._browse(issue_key, comment_id=comment_id),
            "created": str(created.get("created") or ""),
        }

    # --- worklog (ADR 0034, bramkowane) — ewidencja czasu, create-only ---------

    def add_worklog(
        self,
        issue_key: str,
        *,
        time_spent_seconds: int,
        started: str,
        comment: str = "",
        on_behalf_of: str = "",
    ) -> dict[str, Any]:
        """Dopisz wpis czasu; zwróć ``{id, url, created, author_account_id, requested_author}``.

        **Nie wysyłamy pola ``author``** — i nie jest to przeoczenie. Jira Cloud przypisuje
        worklog kontu uwierzytelnionemu tokenem i pole autora w żądaniu IGNORUJE; wysyłanie go
        udawałoby zdolność, której API nie ma. ``on_behalf_of`` wraca więc nietknięte jako
        ``requested_author`` (intencja wołającego), a ``author_account_id`` niesie PRAWDZIWEGO
        autora z odpowiedzi Jiry. Rozjazd tych dwóch pól jest sygnałem dla serwisu, że atrybucja
        jest stratna (ADR 0034).
        """
        payload: dict[str, Any] = {
            "timeSpentSeconds": int(time_spent_seconds),
            "started": started,
        }
        if comment:
            payload["comment"] = text_to_adf(comment)
        with _as_write_error("dodać wpisu czasu"):
            created = self._post_json(
                f"{self._base_url}/rest/api/3/issue/{issue_key}/worklog", payload
            )
        author = _as_dict(created.get("author"))
        return {
            "id": str(created.get("id") or ""),
            "url": self._browse(issue_key),
            "created": str(created.get("created") or ""),
            "author_account_id": str(author.get("accountId") or ""),
            "requested_author": on_behalf_of,
            "time_spent_seconds": int(created.get("timeSpentSeconds") or time_spent_seconds),
        }

    def read_worklogs(self, issue_key: str, *, max_results: int = 100) -> list[dict[str, Any]]:
        """Istniejące wpisy czasu zgłoszenia — podstawa strażnika duplikatów (ADR 0034)."""
        with _as_write_error("odczytać wpisów czasu"):
            data = self._get_json(
                f"{self._base_url}/rest/api/3/issue/{issue_key}/worklog",
                {"maxResults": str(max_results)},
            )
        raw = data.get("worklogs") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            return []
        entries: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            author = _as_dict(item.get("author"))
            entries.append(
                {
                    "id": str(item.get("id") or ""),
                    "author_account_id": str(author.get("accountId") or ""),
                    "started": str(item.get("started") or ""),
                    "time_spent_seconds": int(item.get("timeSpentSeconds") or 0),
                    # ADF → tekst NA GRANICY adaptera, jak reszta treści Cloud (ADR 0033).
                    "comment": adf_to_text(item.get("comment")),
                }
            )
        return entries

    # --- transition (ADR 0032, bramkowane) — best-effort chodzenie po workflow ---

    def read_transitions(self, issue_key: str) -> dict[str, Any]:
        """Bieżący status + dostępne tranzycje (sąsiedzi) jednym GET-em (``expand=transitions``)."""
        with _as_write_error("odczytać tranzycji"):
            data = self._get_json(
                f"{self._base_url}/rest/api/3/issue/{issue_key}",
                {"fields": "status", "expand": "transitions"},
            )
        current = _status_name(data.get("fields") if isinstance(data, dict) else None)
        raw = data.get("transitions") if isinstance(data, dict) else None
        transitions: list[dict[str, str]] = []
        if isinstance(raw, list):
            for t in raw:
                if not isinstance(t, dict):
                    continue
                to = t.get("to")
                transitions.append(
                    {
                        "id": str(t.get("id") or ""),
                        "name": str(t.get("name") or ""),
                        "to_status": str(to.get("name") or "") if isinstance(to, dict) else "",
                    }
                )
        return {"current_status": current, "transitions": transitions}

    def transition_issue(self, issue_key: str, transition_id: str) -> dict[str, Any]:
        """Wykonaj tranzycję (POST ``transition.id``); ``{url, status, updated}`` dobrane GET-em."""
        with _as_write_error("wykonać tranzycji"):
            self._post_no_content(
                f"{self._base_url}/rest/api/3/issue/{issue_key}/transitions",
                {"transition": {"id": transition_id}},
            )
        status, updated = self._fetch_status_updated(issue_key)
        return {"url": self._browse(issue_key), "status": status, "updated": updated}

    def _fetch_status_updated(self, key: str) -> tuple[str, str]:
        """Bieżący status i ``updated`` osobnym GET-em (POST tranzycji zwraca 204). Best-effort."""
        if not key:
            return "", ""
        try:
            data = self._get_json(
                f"{self._base_url}/rest/api/3/issue/{key}", {"fields": "status,updated"}
            )
        except httpx.HTTPError:
            return "", ""
        fields = data.get("fields") if isinstance(data, dict) else None
        if not isinstance(fields, dict):
            return "", ""
        return _status_name(fields), str(fields.get("updated") or "")

    def _fetch_created(self, key: str) -> str:
        """Znacznik ``created`` osobnym GET-em (odpowiedź create go nie niesie). Best-effort."""
        if not key:
            return ""
        try:
            data = self._get_json(f"{self._base_url}/rest/api/3/issue/{key}", {"fields": "created"})
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

    def _post_no_content(self, url: str, payload: dict[str, Any]) -> None:
        """POST bez parsowania ciała — tranzycja zwraca 204 No Content (``.json()`` by padł)."""
        response = self._client.post(url, json=payload)
        response.raise_for_status()


def _normalize_adf(issue: dict[str, Any]) -> None:
    """Spłaszcz ADF → tekst w miejscu: ``fields.description`` i każdy ``comment.body``.

    Dzięki temu ``jira.selection`` (czyta te pola jako stringi) działa na Cloud bez zmian. ``None``
    (brak opisu) zostaje ``None`` — zachowanie jak Server/DC (opis nieobecny → brak podsumowania).
    """
    fields = issue.get("fields")
    if not isinstance(fields, dict):
        return
    description = fields.get("description")
    if description is not None:
        fields["description"] = adf_to_text(description)
    comment = fields.get("comment")
    if isinstance(comment, dict):
        comments = comment.get("comments")
        if isinstance(comments, list):
            for c in comments:
                if isinstance(c, dict) and c.get("body") is not None:
                    c["body"] = adf_to_text(c.get("body"))


def _status_name(fields: Any) -> str:
    """Wyłuskaj nazwę statusu z ``fields.status.name`` (odporne na brak/None)."""
    if not isinstance(fields, dict):
        return ""
    status = fields.get("status")
    return str(status.get("name") or "") if isinstance(status, dict) else ""


def _as_dict(value: Any) -> dict[str, Any]:
    """Zwróć zagnieżdżony obiekt JSON jako słownik albo pusty — odporność na dziwny kształt."""
    return value if isinstance(value, dict) else {}


@contextlib.contextmanager
def _as_write_error(action: str) -> Iterator[None]:
    """Zamień błąd HTTP zapisu na ``WriteError`` — granica: narzędzie zwróci ``{"error": ...}``."""
    try:
        yield
    except httpx.HTTPStatusError as exc:
        raise WriteError(f"nie udało się {action} (HTTP {exc.response.status_code}).") from exc
    except httpx.HTTPError as exc:
        raise WriteError(f"nie udało się {action}: {exc}.") from exc
