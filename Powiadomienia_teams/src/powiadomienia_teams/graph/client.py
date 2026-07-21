"""Synchroniczny klient Microsoft Graph — członkowie zespołu i zmiany (Shifts).

Świadomie synchroniczny (``httpx.Client``): zadanie jest cykliczne (niedziela 16:00), nie
event-loopowe jak poller kanałów — sync upraszcza pętlę i listener odpowiedzi. Obsługuje
429/Retry-After i stronicowanie ``@odata.nextLink``. Token wstrzykiwany przez dostawcę
(``graph/auth.py``); ``refresh_auth`` woła się raz na przebieg.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from powiadomienia_teams.domain.models import Member, Shift, TimeOff
from powiadomienia_teams.graph.auth import AuthExpiredError
from powiadomienia_teams.graph.mapping import (
    member_from_json,
    shift_from_json,
    time_off_from_json,
    to_graph_iso,
)
from powiadomienia_teams.reminders.timeoff import TeamReasons, normalize

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
_DEFAULT_RETRY_AFTER_S = 5
# ŁĄCZNY budżet czekania na dławienie w obrębie jednego żądania. Sufit per-próba zamieniał
# przejściowe dławienie w porzucony przebieg tygodniowy (patrz `_retry_after`).
_MAX_RETRY_BUDGET_S = 900
_MAX_429_RETRIES = 5
_MAX_PAGES = 50
_DEFAULT_THEME = "green"  # nieokreślony dzień = stacjonarnie
_MAX_ERROR_BODY = 500  # ile znaków ciała błędu trafia do logu


class GraphPermissionError(RuntimeError):
    """Graph odmówił dostępu (403) — brak zgody/roli. NIE jest błędem transientnym.

    Wydzielony z ogólnych 4xx, bo ponawianie nic nie da: cofniętej zgody admina ani utraconej roli
    właściciela zespołu nie naprawi kolejna próba. Orkiestracja pomija dla niego backoff.
    """


def _retry_after(response: httpx.Response, pozostaly_budzet: int) -> int:
    """Sekundy do ponowienia z nagłówka Graph, ograniczone POZOSTAŁYM budżetem oczekiwania.

    Ograniczamy SUMĘ czekania, nie pojedynczą przerwę. Wcześniejszy sufit 60 s na próbę wyglądał
    ostrożnie, ale zamieniał „wolno, ale w końcu się uda" w „porzucone": przy dławieniu, w którym
    Graph prosi o 3600 s, pięć prób wyczerpywało się w pięć minut i przebieg tygodniowy padał.
    Z budżetem łącznym czekamy tyle, ile Graph prosi, dopóki mieści się to w rozsądnym oknie —
    a ignorowanie `Retry-After` bywa przez Microsoft karane wydłużeniem dławienia.
    """
    raw = response.headers.get("Retry-After", "")
    czekaj = int(raw) if raw.isdigit() else _DEFAULT_RETRY_AFTER_S
    if czekaj > pozostaly_budzet:
        logger.warning(
            "Graph prosi o %ds przerwy, a budżet oczekiwania to jeszcze %ds — skracam",
            czekaj, pozostaly_budzet,
        )
    return max(0, min(czekaj, pozostaly_budzet))


def _raise_for_status(response: httpx.Response) -> None:
    """``raise_for_status`` wzbogacone o CIAŁO odpowiedzi i rozróżnienie 401/403.

    Samo ``raise_for_status`` daje tylko kod i URL, a jedyne miejsce z prawdziwą przyczyną
    („Missing scope Schedule.ReadWrite.All", „Caller does not have access to the schedule") jest
    w ciele — bez niego diagnoza na serwerze bez terminala jest niemożliwa.
    """
    if response.status_code < 400:
        return
    body = response.text[:_MAX_ERROR_BODY]
    logger.error("Graph %s %s → %s: %s", response.request.method, response.request.url,
                 response.status_code, body)
    if response.status_code == 401:
        # Token odrzucony mimo udanego cichego odświeżenia (cofnięta zgoda, zmiana hasła konta
        # „głosu", nowa polityka Conditional Access). To NIE jest błąd transientny — usługa ma się
        # zatrzymać czysto z instrukcją `--login`, a nie kręcić w pętli udając zdrową.
        raise AuthExpiredError(
            "Graph odrzucił token (401) — zaloguj się ponownie: `powiadomienia-teams --login`."
        )
    if response.status_code == 403:
        raise GraphPermissionError(f"Graph odmówił dostępu (403): {body}")
    response.raise_for_status()


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
        budzet = _MAX_RETRY_BUDGET_S
        while True:
            response = self._client.get(url, params=params)
            if response.status_code == 429 and attempts < _MAX_429_RETRIES and budzet > 0:
                attempts += 1
                czekaj = _retry_after(response, budzet)
                budzet -= czekaj
                self._sleep(czekaj)
                continue
            _raise_for_status(response)
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
        if next_url:
            # Odczyt NIEPEŁNY. Grafik ma jeszcze strony, których nie przeczytaliśmy — a wykrywanie
            # luk („kto nie ma zmian") działa na tym, co wróciło. Ucięcie oznacza więc prośby
            # wysłane osobom, które grafik MAJĄ, i po ich „tak" DRUGI komplet wpisów w Shifts.
            # Bez tego logu objaw wyglądałby na kaprys bota, a nie na przekroczony limit odczytu.
            logger.error(
                "Ucięto stronicowanie %s po %d stronach — odczyt NIEPEŁNY, wykrywanie luk "
                "w grafiku może dawać fałszywe wyniki",
                url, pages,
            )
        return items

    def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        attempts = 0
        budzet = _MAX_RETRY_BUDGET_S
        while True:
            response = self._client.post(url, json=body)
            if response.status_code == 429 and attempts < _MAX_429_RETRIES and budzet > 0:
                attempts += 1
                czekaj = _retry_after(response, budzet)
                budzet -= czekaj
                self._sleep(czekaj)
                continue
            _raise_for_status(response)
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

    def read_time_off(
        self, team_id: str, window_start: datetime, window_end: datetime
    ) -> tuple[TimeOff, ...]:
        """Czas wolny PRZECINAJĄCY się z ``[window_start, window_end)`` (tz-aware, UTC).

        Kryterium PRZECIĘCIA, nie „początek w oknie" jak w ``read_shifts``: zmiana trwa najwyżej
        24 h, więc jej początek zawsze wpada w tydzień, ale urlop bywa wielotygodniowy i może
        zacząć się na długo przed oknem docelowym. Gdyby liczyć po początku, osoba w środku
        dwutygodniowego urlopu wyszłaby jako „bez grafiku" i dostałaby prośbę.
        """
        raw = self._get_all(f"{GRAPH}/teams/{team_id}/schedule/timesOff")
        entries = [
            t
            for t in (time_off_from_json(x) for x in raw)
            if t is not None and t.start < window_end and t.end > window_start
        ]
        return tuple(sorted(entries, key=lambda t: t.start))

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

    def send_chat_message(self, chat_id: str, html: str) -> str:
        """Wyślij wiadomość HTML do czatu; zwróć ``createdDateTime`` (czas SERWERA) wiadomości.

        Ten znacznik służy jako watermark przypomnienia — czas serwera (a nie lokalnego zegara)
        chroni przed przesunięciem zegarów: odpowiedź pracownika ma zawsze późniejszy znacznik
        serwera niż wysłane przez nas przypomnienie.
        """
        data = self._post(
            f"{GRAPH}/chats/{chat_id}/messages",
            {"body": {"contentType": "html", "content": html}},
        )
        return str(data.get("createdDateTime", ""))

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
            "startDateTime": to_graph_iso(shift.start),
            "endDateTime": to_graph_iso(shift.end),
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
            {
                "notifyTeam": notify,
                "startDateTime": to_graph_iso(start),
                "endDateTime": to_graph_iso(end),
            },
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
                "startDateTime": to_graph_iso(time_off.start),
                "endDateTime": to_graph_iso(time_off.end),
            },
        }
        return str(self._post(f"{GRAPH}/teams/{team_id}/schedule/timesOff", body).get("id", ""))
