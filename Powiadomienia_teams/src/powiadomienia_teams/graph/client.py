"""Synchroniczny klient Microsoft Graph — członkowie zespołu i zmiany (Shifts).

Świadomie synchroniczny (``httpx.Client``): zadanie jest cykliczne (niedziela 16:00), nie
event-loopowe jak poller kanałów — sync upraszcza pętlę i listener odpowiedzi. Obsługuje
429/Retry-After i stronicowanie ``@odata.nextLink``. Token wstrzykiwany przez dostawcę
(``graph/auth.py``); ``refresh_auth`` woła się raz na przebieg.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from powiadomienia_teams.domain.models import Member, Shift, TimeOff
from powiadomienia_teams.graph.mapping import member_from_json, shift_from_json
from powiadomienia_teams.reminders.timeoff import TeamReasons, normalize

GRAPH = "https://graph.microsoft.com/v1.0"
_UTC = timezone.utc
_DEFAULT_RETRY_AFTER_S = 5
_MAX_429_RETRIES = 5
_MAX_PAGES = 50
_DEFAULT_THEME = "green"  # nieokreślony dzień = stacjonarnie


def _retry_after(response: httpx.Response) -> int:
    raw = response.headers.get("Retry-After", "")
    return int(raw) if raw.isdigit() else _DEFAULT_RETRY_AFTER_S


def _iso_z(dt: datetime) -> str:
    """UTC datetime → ISO 8601 z sufiksem ``Z`` (format oczekiwany przez Graph)."""
    return dt.astimezone(_UTC).isoformat().replace("+00:00", "Z")


class GraphClient:
    """Klient Graph oparty o ``httpx.Client`` i synchronicznego dostawcę tokenu."""

    def __init__(
        self,
        client: httpx.Client,
        token_provider: Callable[[], str],
        *,
        sleep: Callable[[int], None] = time.sleep,
    ) -> None:
        self._client = client
        self._token = token_provider
        self._sleep = sleep

    def refresh_auth(self) -> None:
        """Ustaw nagłówek Authorization świeżym tokenem (MSAL zwykle odświeża po cichu)."""
        self._client.headers["Authorization"] = f"Bearer {self._token()}"

    def _get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        attempts = 0
        while True:
            response = self._client.get(url, params=params)
            if response.status_code == 429 and attempts < _MAX_429_RETRIES:
                attempts += 1
                self._sleep(_retry_after(response))
                continue
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return data

    def _get_all(self, url: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        pages = 0
        next_url: str | None = url
        while next_url and pages < _MAX_PAGES:
            data = self._get(next_url, params=params if pages == 0 else None)
            items.extend(data.get("value", []))
            next_url = data.get("@odata.nextLink")
            pages += 1
        return items

    def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        attempts = 0
        while True:
            response = self._client.post(url, json=body)
            if response.status_code == 429 and attempts < _MAX_429_RETRIES:
                attempts += 1
                self._sleep(_retry_after(response))
                continue
            response.raise_for_status()
            data: dict[str, Any] = response.json() if response.content else {}
            return data

    def get_me(self) -> str:
        """Id zalogowanego użytkownika (tożsamość »głosu« bota)."""
        return str(self._get(f"{GRAPH}/me").get("id", ""))

    def schedule_provision_status(self, team_id: str) -> str | None:
        """``provisionStatus`` grafiku zespołu (``Completed`` = Shifts uruchomione)."""
        return self._get(f"{GRAPH}/teams/{team_id}/schedule").get("provisionStatus")

    def list_members(self, team_id: str) -> tuple[Member, ...]:
        """Aktualni członkowie zespołu (roster do porównania). Pomija wpisy bez userId/nazwy."""
        raw = self._get_all(f"{GRAPH}/teams/{team_id}/members")
        return tuple(m for m in (member_from_json(x) for x in raw) if m is not None)

    def read_shifts(
        self, team_id: str, window_start: datetime, window_end: datetime
    ) -> tuple[Shift, ...]:
        """Zmiany zaczynające się w ``[window_start, window_end)`` (tz-aware, UTC), posortowane.

        Filtrowanie po stronie klienta (endpoint ``/schedule/shifts`` bywa kapryśny na
        ``$filter``); zmiany-sieroty po byłych członkach są tu wciąż zwracane — odsiewa je
        dopiero wykrywanie luk po aktualnym rosterze.
        """
        raw = self._get_all(f"{GRAPH}/teams/{team_id}/schedule/shifts")
        shifts = [
            s
            for s in (shift_from_json(x) for x in raw)
            if s is not None and window_start <= s.start < window_end
        ]
        return tuple(sorted(shifts, key=lambda s: s.start))

    def create_or_get_chat(self, me_id: str, target_user_id: str) -> str:
        """Utwórz (lub pobierz istniejący) czat 1:1 z pracownikiem — zwróć chat_id.

        Dla ``oneOnOne`` Graph zwraca istniejący czat, jeśli już jest (idempotentne).
        """

        def _member(user_id: str) -> dict[str, Any]:
            return {
                "@odata.type": "#microsoft.graph.aadUserConversationMember",
                "roles": ["owner"],
                "user@odata.bind": f"{GRAPH}/users('{user_id}')",
            }

        body = {"chatType": "oneOnOne", "members": [_member(me_id), _member(target_user_id)]}
        return str(self._post(f"{GRAPH}/chats", body).get("id", ""))

    def send_chat_message(self, chat_id: str, html: str) -> None:
        """Wyślij wiadomość HTML do czatu (root, nie reply)."""
        self._post(
            f"{GRAPH}/chats/{chat_id}/messages",
            {"body": {"contentType": "html", "content": html}},
        )

    def list_chat_messages(self, chat_id: str, *, top: int = 20) -> list[dict[str, Any]]:
        """Ostatnie wiadomości czatu (do wykrywania odpowiedzi pracownika)."""
        data = self._get(f"{GRAPH}/chats/{chat_id}/messages", params={"$top": str(top)})
        return list(data.get("value", []))

    def create_shift(self, team_id: str, shift: Shift) -> str:
        """Utwórz opublikowaną zmianę (``sharedShift``) dla pracownika — zwróć id zmiany.

        Kolor (``theme``) = tryb pracy; kopiowany z dnia źródłowego. Gdy nieznany (nowy dzień),
        domyślnie ``green`` (stacjonarnie).
        """
        shared: dict[str, Any] = {
            "startDateTime": _iso_z(shift.start),
            "endDateTime": _iso_z(shift.end),
            "theme": shift.theme or _DEFAULT_THEME,
        }
        body: dict[str, Any] = {
            "userId": shift.user_id,
            "schedulingGroupId": shift.scheduling_group_id,
            "sharedShift": shared,
        }
        return str(self._post(f"{GRAPH}/teams/{team_id}/schedule/shifts", body).get("id", ""))

    def share_schedule(
        self, team_id: str, start: datetime, end: datetime, *, notify: bool = True
    ) -> None:
        """Udostępnij grafik w zakresie dat (uwidacznia zmiany + opcjonalnie powiadamia)."""
        self._post(
            f"{GRAPH}/teams/{team_id}/schedule/share",
            {"notifyTeam": notify, "startDateTime": _iso_z(start), "endDateTime": _iso_z(end)},
        )

    def list_time_off_reasons(self, team_id: str) -> TeamReasons:
        """Aktywne powody czasu wolnego zespołu (wyszukiwanie po nazwie + odwrotne po id).

        Odpowiada zakładce »dodaj czas wolny« w Shifts (urlop, nieobecność, zwolnienie …).
        Pomija nieaktywne i wpisy bez id/nazwy.
        """
        raw = self._get_all(f"{GRAPH}/teams/{team_id}/schedule/timeOffReasons")
        by_name: dict[str, str] = {}
        names: dict[str, str] = {}
        for item in raw:
            reason_id = item.get("id")
            name = item.get("displayName")
            if item.get("isActive") and reason_id and name:
                by_name[normalize(str(name))] = str(reason_id)
                names[str(reason_id)] = str(name)
        return TeamReasons(by_name=by_name, names=names)

    def create_time_off(self, team_id: str, time_off: TimeOff) -> str:
        """Utwórz opublikowany czas wolny (``sharedTimeOff``) dla pracownika — zwróć jego id.

        Publikuje od razu (jak ``create_shift`` z ``sharedShift``), więc pracownik widzi wpis
        w zakładce »Zmiany« bez osobnego udostępniania.
        """
        body: dict[str, Any] = {
            "userId": time_off.user_id,
            "sharedTimeOff": {
                "timeOffReasonId": time_off.reason_id,
                "startDateTime": _iso_z(time_off.start),
                "endDateTime": _iso_z(time_off.end),
            },
        }
        return str(self._post(f"{GRAPH}/teams/{team_id}/schedule/timesOff", body).get("id", ""))
