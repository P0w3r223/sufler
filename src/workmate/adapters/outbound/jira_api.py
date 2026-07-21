"""Klient Jira Server/DC REST v2 (``httpx.Client`` + PAT Bearer) — porty read/write Jira.

Importowany LENIWIE (wymaga extra ``jira`` — ``httpx``). Sync (nie async): poller/narzędzia wołają
w puli wątków (jak ``github_api``). PAT to SEKRET — wstrzykiwany, nigdy logowany. Paginacja po
``startAt`` (odpowiedź ``/search`` to KOPERTA ``{issues, total, startAt, maxResults}``, nie goła
lista). Zapis (Gate 5 / ADR 0031) jest CREATE-ONLY i tłumaczy błąd HTTP na domenowy ``WriteError``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import httpx

from workmate.core.errors import WriteError

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
        """Dopisz wpis czasu (REST v2, komentarz zwykłym tekstem — bez ADF).

        **Nie wysyłamy pola ``author``**, tak samo jak na Cloud. Na Server/DC dałoby się przy
        odpowiednich uprawnieniach ustawić autora, ale świadomie tego NIE robimy: jedno
        zachowanie na obu wdrożeniach jest łatwiejsze do wytłumaczenia użytkownikowi niż
        atrybucja zależna od tego, gdzie stoi instancja (ADR 0034). Gdy pojawi się potrzeba
        prawdziwej atrybucji, wejdzie ona osobną strategią, nie cichym rozjazdem adapterów.
        """
        payload: dict[str, Any] = {
            "timeSpentSeconds": int(time_spent_seconds),
            "started": started,
        }
        if comment:
            payload["comment"] = comment
        with _as_write_error("dodać wpisu czasu"):
            created = self._post_json(
                f"{self._base_url}/rest/api/2/issue/{issue_key}/worklog", payload
            )
        author = _as_dict(created.get("author"))
        return {
            "id": str(created.get("id") or ""),
            "url": self._browse(issue_key),
            "created": str(created.get("created") or ""),
            # Server/DC identyfikuje konto przez ``name``/``key``, nie ``accountId`` — port
            # nazywa to pole jednakowo, bo serwis porównuje je tylko z ``self_account``.
            "author_account_id": str(author.get("name") or author.get("key") or ""),
            "requested_author": on_behalf_of,
            "time_spent_seconds": int(created.get("timeSpentSeconds") or time_spent_seconds),
        }

    def read_worklogs(self, issue_key: str, *, max_results: int = 100) -> list[dict[str, Any]]:
        """Istniejące wpisy czasu zgłoszenia — podstawa strażnika duplikatów (ADR 0034)."""
        with _as_write_error("odczytać wpisów czasu"):
            data = self._get_json(
                f"{self._base_url}/rest/api/2/issue/{issue_key}/worklog",
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
                    "author_account_id": str(author.get("name") or author.get("key") or ""),
                    "started": str(item.get("started") or ""),
                    "time_spent_seconds": int(item.get("timeSpentSeconds") or 0),
                    "comment": str(item.get("comment") or ""),
                }
            )
        return entries

    # --- transition (ADR 0032, bramkowane) — best-effort chodzenie po workflow ---

    def read_transitions(self, issue_key: str) -> dict[str, Any]:
        """Bieżący status + dostępne tranzycje (sąsiedzi) jednym GET-em (``expand=transitions``).

        Owinięte w ``_as_write_error``: 404 (brak issue) / 403 (brak uprawnień) → ``WriteError``,
        żeby serwis dostał czytelny błąd zamiast wywrócenia tury. API zwraca tranzycje TYLKO
        z bieżącego statusu (nie cały graf) — serwis chodzi po nich hop po hopie.
        """
        with _as_write_error("odczytać tranzycji"):
            data = self._get_json(
                f"{self._base_url}/rest/api/2/issue/{issue_key}",
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
        """Wykonaj tranzycję (POST ``transition.id``); zwróć ``{url, status, updated}``.

        ``POST /transitions`` zwraca 204 bez ciała, więc status/updated dobieramy osobnym GET-em
        POZA ``_as_write_error`` (nieudane dobranie nie zamienia tranzycji, która się PODAŁA, na
        błąd — wtedy pusty ``updated`` i serwis pominie echo tego hopa).
        """
        with _as_write_error("wykonać tranzycji"):
            self._post_no_content(
                f"{self._base_url}/rest/api/2/issue/{issue_key}/transitions",
                {"transition": {"id": transition_id}},
            )
        status, updated = self._fetch_status_updated(issue_key)
        return {"url": self._browse(issue_key), "status": status, "updated": updated}

    def _fetch_status_updated(self, key: str) -> tuple[str, str]:
        """Bieżący status i ``updated`` osobnym GET-em (POST tranzycji zwraca 204, bez ciała).

        Best-effort (jak ``_fetch_created``): potrzebne do echa i potwierdzenia statusu; błąd GET →
        puste, serwis pominie echo tego hopa. Dlatego POZA ``_as_write_error``.
        """
        if not key:
            return "", ""
        try:
            data = self._get_json(
                f"{self._base_url}/rest/api/2/issue/{key}", {"fields": "status,updated"}
            )
        except httpx.HTTPError:
            return "", ""
        fields = data.get("fields") if isinstance(data, dict) else None
        if not isinstance(fields, dict):
            return "", ""
        return _status_name(fields), str(fields.get("updated") or "")

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

    def _post_no_content(self, url: str, payload: dict[str, Any]) -> None:
        """POST bez parsowania ciała — tranzycja zwraca 204 No Content (``.json()`` by padł)."""
        response = self._client.post(url, json=payload)
        response.raise_for_status()


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


def build_jira_client(
    sync_http: httpx.Client, settings: JiraSettings
) -> HttpxJiraClient | HttpxJiraCloudClient:
    """Fabryka klienta Jira wg ``settings.deployment`` (ADR 0033) — JEDNO źródło wyboru providera.

    ``cloud`` → ``HttpxJiraCloudClient`` (Basic ``email:api_token``, REST v3/ADF, ``search/jql``);
    inaczej ``HttpxJiraClient`` (Server/DC — PAT Bearer, REST v2). Oba spełniają porty read/write,
    więc poller i serwisy zapisu ich nie rozróżniają. Klient Cloud ładowany LENIWIE (gdy trzeba).
    """
    if settings.deployment.strip().lower() == "cloud":
        from workmate.adapters.outbound.jira_cloud_api import HttpxJiraCloudClient

        return HttpxJiraCloudClient(
            sync_http, email=settings.email, token=settings.token, base_url=settings.base_url
        )
    return HttpxJiraClient(sync_http, settings.token, base_url=settings.base_url)
