"""Synchroniczny klient Microsoft Graph — członkowie zespołu i zmiany (Shifts).

Świadomie synchroniczny (``httpx.Client``): zadanie jest cykliczne (niedziela 16:00), nie
event-loopowe jak poller kanałów — sync upraszcza pętlę i listener odpowiedzi. Obsługuje
429/Retry-After i stronicowanie ``@odata.nextLink``. Token wstrzykiwany przez dostawcę
(``graph/auth.py``); ``refresh_auth`` woła się raz na przebieg.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import httpx

from powiadomienia_teams.domain.czas import to_graph_iso
from powiadomienia_teams.domain.models import Member, Shift, TimeOff
from powiadomienia_teams.domain.powody import TeamReasons, normalize
from powiadomienia_teams.graph.auth import AuthExpiredError
from powiadomienia_teams.graph.mapping import (
    member_from_json,
    shift_from_json,
    time_off_from_json,
)

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
_DEFAULT_RETRY_AFTER_S = 5
# ŁĄCZNY budżet czekania na dławienie w obrębie jednego żądania. Sufit per-próba zamieniał
# przejściowe dławienie w porzucony przebieg tygodniowy (patrz `_retry_after`).
_MAX_RETRY_BUDGET_S = 900
_MAX_429_RETRIES = 5
_MAX_PAGES = 50
# Sufit jednej strony historii czatu (maksimum przyjmowane przez ten endpoint). Przy 20 —
# poprzedniej wartości — pracownik piszący serię krótkich dymków między zajrzeniami tracił
# najstarsze z nich bezpowrotnie.
_MAX_WIADOMOSCI_CZATU = 50
# Próg wczesnego ostrzegania. Kolekcja zmian zespołu rośnie co tydzień i nigdy nie maleje, więc
# sufit stronicowania ZOSTANIE kiedyś przekroczony — a wtedy przebieg pada i będzie padał
# identycznie w każdy kolejny tydzień (przyczyna nie jest przejściowa). Bez tego progu przejście
# od „działa" do „nie działa nigdy" nie ma żadnego sygnału pośredniego.
_MAX_PAGES_OSTRZEZENIE = 30
# Jak rzadko ten sam odczyt może zaalarmować operatora. Próg wyżej mówi „kiedy warto krzyknąć",
# ten odstęp — „ile razy". Bez niego przekroczenie progu zamienia KAŻDY odczyt w wiadomość na
# webhooku, a że kolekcja nigdy nie maleje, kanał alertowy zapycha się na stałe od dnia, w którym
# ostrzeżenie po raz pierwszy miało sens. Alert, który przychodzi bez przerwy, przestaje być
# alertem — a tym samym kanałem idą rzeczy krytyczne (utrata sesji, awaria zapisu).
_ODSTEP_OSTRZEZENIA_S = 24 * 3600
_DEFAULT_THEME = "green"  # nieokreślony dzień = stacjonarnie
_MAX_ERROR_BODY = 500  # ile znaków ciała błędu trafia do logu


class GraphTruncatedReadError(RuntimeError):
    """Odczyt kolekcji urwał się na limicie stron — dane są NIEPEŁNE i nie wolno ich użyć.

    Wydzielony wyjątek zamiast zwrócenia części wyników: wykrywanie luk („kto nie ma zmian")
    pracuje na tym, co wróciło, więc niepełny odczyt oznacza prośby do osób, które grafik MAJĄ,
    a po ich »tak« DRUGI komplet wpisów w Shifts. Skutkiem jest nieodwracalny zapis u klienta,
    dlatego przebieg ma paść i zostać ponowiony, a nie „udać się" na połowie danych.
    """


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
    if raw and not raw.isdigit():
        # Nagłówek dopuszcza także HTTP-date. Nie parsujemy go (Graph w praktyce oddaje sekundy),
        # ale cichy zjazd do kilku sekund przy prośbie o godzinę oznacza dobijanie się do usługi,
        # która właśnie prosi o spokój — a ignorowanie `Retry-After` bywa karane wydłużeniem
        # dławienia. Skoro nie umiemy tego odczytać, niech przynajmniej będzie widać.
        logger.warning(
            "Nagłówek Retry-After nie jest liczbą sekund (%r) — przyjmuję %ds",
            raw, _DEFAULT_RETRY_AFTER_S,
        )
    czekaj = int(raw) if raw.isdigit() else _DEFAULT_RETRY_AFTER_S
    if czekaj > pozostaly_budzet:
        logger.warning(
            "Graph prosi o %ds przerwy, a budżet oczekiwania to jeszcze %ds — skracam",
            czekaj, pozostaly_budzet,
        )
    return max(0, min(czekaj, pozostaly_budzet))


def _zglos_odrzucone(wszystkich: int, przyjetych: int, co: str) -> None:
    """Zgłoś rekordy, których nie dało się zmapować — cisza tutaj kosztuje tyle, co ucięty odczyt.

    Mappery zwracają ``None`` przy brakującym polu, złej dacie albo naruszeniu kontraktu
    domenowego, a odbiorcy odsiewają te ``None`` bez śladu. Skutek jest DOKŁADNIE ten sam, przed
    którym broni ``GraphTruncatedReadError``: zgubiona zmiana → osoba widziana jako „bez grafiku"
    → prośba do kogoś, kto grafik ma → po jej »tak« drugi komplet wpisów w Shifts. Nie przechodzimy
    tu na fail-closed (pojedynczy dziwny rekord nie powinien kłaść tygodnia), ale zjawisko musi być
    widoczne, zanim urośnie.
    """
    odrzucone = wszystkich - przyjetych
    if odrzucone > 0:
        logger.warning("Pominięto %d z %d wpisów (%s) — nie dało się ich zmapować",
                       odrzucone, wszystkich, co)


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


def _bez_limitu_czasu(_za_ile_s: float = 0.0) -> None:
    """Domyślny brak sufitu czasu przebiegu — klient bez wpiętego budżetu działa jak dotąd."""


class GraphClient:
    """Klient Graph oparty o ``httpx.Client`` i synchronicznego dostawcę tokenu."""

    def __init__(
        self,
        client: httpx.Client,
        token_provider: Callable[[], str],
        *,
        sleep: Callable[[int], None] = time.sleep,
        ostrzegaj: Callable[[str, str], None] | None = None,
        teraz: Callable[[], datetime] | None = None,
        sprawdz_czas: Callable[[float], None] | None = None,
    ) -> None:
        self._client = client
        self._token = token_provider
        self._sleep = sleep
        # Sufit czasu na cały przebieg, wstrzykiwany tak samo jak `ostrzegaj`: klient nie zna
        # pojęcia „przebieg", a mimo to potrafi się na jego granicy zatrzymać. Callable, a nie
        # termin, bo okno przestawia się przy każdym przebiegu, a klient powstaje raz na proces.
        # Argument to długość czekania, które klient ZAMIERZA rozpocząć — patrz `runtime.budzet`.
        # Brak (testy, skrypty) oznacza brak ograniczenia — dokładnie jak dotąd.
        self._sprawdz_czas: Callable[[float], None] = sprawdz_czas or _bez_limitu_czasu
        # Kanał ostrzeżeń operatorskich, wstrzykiwany jak `sleep` — klient nie zna `Settings` ani
        # webhooka, a i tak potrafi zgłosić zbliżanie się do sufitu stronicowania. Brak kanału
        # (testy, skrypty) oznacza tylko log.
        self._ostrzegaj = ostrzegaj
        # Zegar wstrzykiwany z tego samego powodu co `sleep`: bez niego test odstępu między
        # ostrzeżeniami musiałby czekać dobę.
        self._teraz = teraz or (lambda: datetime.now(timezone.utc))
        self._ostatnie_ostrzezenie: dict[str, datetime] = {}

    def refresh_auth(self) -> None:
        """Ustaw nagłówek Authorization świeżym tokenem (MSAL zwykle odświeża po cichu)."""
        self._client.headers["Authorization"] = f"Bearer {self._token()}"

    @contextmanager
    def bez_limitu_czasu(self) -> Iterator[None]:
        """Zawieś sufit czasu przebiegu na czas bloku — dla pracy, której NIE WOLNO przerwać w pół.

        Jedyny taki blok to zapis do grafiku. Po commicie ``APPLYING`` i pierwszym ``create_shift``
        przerwanie zostawia u klienta pół tygodnia i nikt nie wie, które dni doszły; dokończenie
        kosztuje kilka żądań. Limit istnieje po to, żeby przebieg nie zjadał godzin na ODCZYTACH,
        a nie po to, żeby rozerwać zapis w połowie.

        Ryzyko jest ograniczone z konstrukcji, inaczej byłaby to furtka na oścież: liczba zapisów
        to najwyżej kilkanaście wpisów jednego tygodnia, a każde żądanie ma własny budżet
        ``_MAX_RETRY_BUDGET_S`` — w przeciwieństwie do odczytu, który idzie przez ``_MAX_PAGES``
        stron i to on odpowiada za godziny, których limit ma pilnować.
        """
        poprzedni = self._sprawdz_czas
        self._sprawdz_czas = _bez_limitu_czasu
        try:
            yield
        finally:
            self._sprawdz_czas = poprzedni

    def _zadanie(self, wykonaj: Callable[[], httpx.Response]) -> httpx.Response:
        """Jedno żądanie z obsługą dławienia (429) i jednorazowym odświeżeniem tokenu (401).

        Odświeżenie po 401 nie jest ostrożnością, tylko poprawką błędnej diagnozy. ``refresh_auth``
        woła się RAZ na przebieg, a jeden przebieg trwa długo: budżet czekania na ``Retry-After``
        to 900 s NA ŻĄDANIE, a ``_get_all`` wykonuje ich do ``_MAX_PAGES``. Access-token z cache
        MSAL bywa wtedy już przeterminowany — czyli 401 mówi „token się zestarzał", a nie „cofnięto
        zgodę". Bez tego rozróżnienia jedno dłuższe dławienie Graph kończyło się alertem
        KRYTYCZNYM „Utracono uwierzytelnienie", instrukcją ``--login``, która niczego nie naprawia,
        i utraconym tygodniem: ``_run_once_with_retry`` NIE ponawia ``AuthExpiredError``.

        Odświeżamy najwyżej raz. Drugie 401 po świeżym tokenie to już realna utrata dostępu —
        ``refresh_auth`` rzuci ``AuthExpiredError`` samo, gdy sesja naprawdę wygasła.
        """
        attempts = 0
        budzet = _MAX_RETRY_BUDGET_S
        odswiezono = False
        while True:
            # Dwa sprawdzenia sufitu czasu przebiegu, bo są to dwa jedyne miejsca, w których ta
            # metoda potrafi pochłonąć godziny: kolejne żądanie (`_get_all` woła ją do `_MAX_PAGES`
            # razy) i czekanie na `Retry-After` (do `_MAX_RETRY_BUDGET_S` na każde żądanie).
            # Drugie sprawdzenie dostaje DŁUGOŚĆ planowanego czekania, więc przerywa PRZED nim,
            # a nie po — inaczej limit obowiązywałby z dokładnością do jednego pełnego dławienia.
            self._sprawdz_czas(0.0)
            response = wykonaj()
            if response.status_code == 429 and attempts < _MAX_429_RETRIES and budzet > 0:
                attempts += 1
                czekaj = _retry_after(response, budzet)
                budzet -= czekaj
                self._sprawdz_czas(float(czekaj))
                self._sleep(czekaj)
                continue
            if response.status_code == 401 and not odswiezono:
                odswiezono = True
                logger.info("Graph odrzucił token (401) — odświeżam i ponawiam żądanie raz.")
                # Odświeżenie to WYJŚCIE SIECIOWE do Entra ID z własnym limitem (`_MSAL_TIMEOUT_S`),
                # więc też należy do czasu przebiegu — bez tego sprawdzenia zdanie „limit obejmuje
                # wszystkie miejsca, w których ta metoda potrafi czekać" byłoby nieprawdziwe.
                self._sprawdz_czas(0.0)
                self.refresh_auth()
                continue
            _raise_for_status(response)
            return response

    def _get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        response = self._zadanie(lambda: self._client.get(url, params=params))
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
            # Sam log tu nie wystarczy: usługa bezobsługowa bez monitoringu logów zachowuje się
            # wtedy tak, jakby nic się nie stało — a skutkiem jest podwójny zapis do grafiku
            # klienta. Fail-closed: przebieg pada, `_run_once_with_retry` ponawia, a przy trwałym
            # przekroczeniu limitu operator dostaje alert zamiast bota wysyłającego złe prośby.
            raise GraphTruncatedReadError(
                f"Ucięto stronicowanie {url} po {pages} stronach — odczyt NIEPEŁNY, "
                f"wykrywanie luk w grafiku dałoby fałszywe wyniki"
            )
        if pages >= _MAX_PAGES_OSTRZEZENIE:
            tresc = (
                f"Odczyt {url} zajął {pages} z {_MAX_PAGES} dopuszczalnych stron. Kolekcja rośnie "
                f"i nie maleje — po przekroczeniu limitu przebieg tygodniowy zacznie padać TRWALE."
            )
            # Log przy KAŻDYM odczycie, alert najwyżej raz na dobę na URL. To nie jest ta sama
            # decyzja dwa razy: log jest trwałym śladem, po którym da się potem odtworzyć tempo
            # narastania problemu, a webhook to kanał, w którym nadmiar wypycha rzeczy ważne.
            logger.warning("%s", tresc)
            if self._ostrzegaj is not None and self._czas_na_ostrzezenie(url):
                self._ostrzegaj("Odczyt Graph zbliża się do limitu stron", tresc)
        return items

    def _czas_na_ostrzezenie(self, url: str) -> bool:
        """Czy minął odstęp od ostatniego alertu dla TEGO odczytu (osobno dla każdej kolekcji).

        Klucz to URL, nie „jedno ostrzeżenie na klienta": zmiany i czas wolny rosną niezależnie,
        a wiadomość „to już 31 stron" jest bezużyteczna, jeśli nie wiadomo, czego dotyczy.

        Pamięć żyje w instancji klienta, a ta powstaje raz na proces (``cli.main``), więc odstęp
        obowiązuje przez cały czas działania usługi. Restart kontenera zeruje ją świadomie: po
        restarcie operator i tak patrzy w logi, a pierwsze ostrzeżenie po podniesieniu usługi
        jest wtedy informacją, nie szumem.
        """
        teraz = self._teraz()
        ostatnie = self._ostatnie_ostrzezenie.get(url)
        if ostatnie is not None and (teraz - ostatnie).total_seconds() < _ODSTEP_OSTRZEZENIA_S:
            return False
        self._ostatnie_ostrzezenie[url] = teraz
        return True

    def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._zadanie(lambda: self._client.post(url, json=body))
        data: dict[str, Any] = response.json() if response.content else {}
        return data

    def get_me(self) -> str:
        """Id zalogowanego użytkownika (tożsamość »głosu« bota). Pusty wynik jest błędem, nie danymi.

        Puste id nie jest neutralne: filtr ``m.user_id != me_id`` w ``runtime.nudge.run_once`` przestaje
        wtedy odsiewać konto bota (bot pisze sam do siebie), a ``create_or_get_chat`` buduje
        ``users('')`` i dostaje 400 dla KAŻDEJ osoby. Wyjątki łapie izolacja per-osoba, więc
        przebieg kończyłby się „sukcesem" bez jednej wysłanej prośby i bez alertu.
        """
        me = str(self._get(f"{GRAPH}/me").get("id", ""))
        if not me:
            raise RuntimeError("Graph /me nie zwrócił id — nie wiadomo, czyją tożsamością pisać")
        return me

    def list_members(self, team_id: str) -> tuple[Member, ...]:
        """Aktualni członkowie zespołu (roster do porównania). Pomija wpisy bez userId/nazwy."""
        raw = self._get_all(f"{GRAPH}/teams/{team_id}/members")
        czlonkowie = tuple(m for m in (member_from_json(x) for x in raw) if m is not None)
        _zglos_odrzucone(len(raw), len(czlonkowie), "członków zespołu")
        return czlonkowie

    def read_shifts(
        self, team_id: str, window_start: datetime, window_end: datetime
    ) -> tuple[Shift, ...]:
        """Zmiany zaczynające się w ``[window_start, window_end)`` (tz-aware, UTC), posortowane.

        Filtrowanie PO STRONIE KLIENTA jest tu decyzją, nie zaniedbaniem — sprawdzone
        w dokumentacji Graph (2026-08-12), zanim ktokolwiek spróbuje to „poprawić":

        1. ``$filter`` nie dopuszcza tej samej właściwości dwa razy, więc okna nie da się wyrazić
           wprost przez ``startDateTime ge X and startDateTime lt Y``.
        2. Filtr po ``sharedShift`` NIE WIDZI zmian istniejących wyłącznie jako ``draftShift``
           (a ``shift_from_json`` przyjmuje je jako fallback). Osoba z grafikiem w wersji roboczej
           wyszłaby jako „bez grafiku": dostałaby prośbę, a sprawdzenie świeżości przed zapisem nie
           zobaczyłoby jej wpisów — czyli DRUGI komplet w grafiku klienta.
        3. Filtr po ``draftShift`` wymaga roli owner (mamy ją), ale dokumentowany przykład łączy
           obie wersje przez ``and``, więc zwraca wyłącznie wpisy mające jednocześnie shared
           i draft — semantyka węższa niż brak filtru.
        4. Zgłoszenie o cichej utracie danych przy dokładnie tym kształcie filtru pozostaje
           w MS Q&A bez odpowiedzi od 2020.

        Cichy brak danych na ścieżce odczytu kończy się złym zapisem, a sufit stronicowania
        kończy się głośną awarią z alertem. Do czasu weryfikacji na żywym tenancie wybieramy
        awarię głośną. Zmiany-sieroty po byłych członkach są tu wciąż zwracane — odsiewa je
        dopiero wykrywanie luk po aktualnym rosterze.
        """
        raw = self._get_all(f"{GRAPH}/teams/{team_id}/schedule/shifts")
        zmapowane = [s for s in (shift_from_json(x) for x in raw) if s is not None]
        _zglos_odrzucone(len(raw), len(zmapowane), "zmian")
        shifts = [s for s in zmapowane if window_start <= s.start < window_end]
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
        zmapowane = [t for t in (time_off_from_json(x) for x in raw) if t is not None]
        _zglos_odrzucone(len(raw), len(zmapowane), "wpisów czasu wolnego")
        entries = [t for t in zmapowane if t.start < window_end and t.end > window_start]
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
        chat_id = str(self._post(f"{GRAPH}/chats", body).get("id", ""))
        if not chat_id:
            # Pusty chat_id trafiłby do stanu i nigdy nie dałby się użyć: listener czytałby
            # `chats//messages`, więc odpowiedź pracownika byłaby trwale niewidoczna.
            raise RuntimeError(f"Graph nie zwrócił id czatu dla {target_user_id}")
        return chat_id

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

    def list_chat_messages(
        self, chat_id: str, *, top: int = _MAX_WIADOMOSCI_CZATU
    ) -> list[dict[str, Any]]:
        """Ostatnie wiadomości czatu (do wykrywania odpowiedzi pracownika).

        Bez stronicowania — i to jest decyzja, nie przeoczenie: interesują nas wyłącznie
        wiadomości NOWSZE od watermarku, a te są na początku (kolejność malejąca po czasie).
        Ryzyko jest jedno: gdyby między dwoma zajrzeniami przyszło więcej niż ``top`` wiadomości,
        najstarsze wypadłyby poza stronę i przepadły bezpowrotnie, bo watermark przeskakuje na
        najnowszą. Odstęp odpytywania rośnie do godziny, więc przypadek jest realny dla kogoś,
        kto pisze serią krótkich dymków.

        Dlatego pełna strona jest ZGŁASZANA, a nie przemilczana — cichy limit na ścieżce odczytu
        odpowiedzi to ta sama klasa błędu, którą przy grafiku zamyka ``GraphTruncatedReadError``.
        """
        data = self._get(f"{GRAPH}/chats/{chat_id}/messages", params={"$top": str(top)})
        wiadomosci = list(data.get("value", []))
        if len(wiadomosci) >= top:
            logger.warning(
                "Historia czatu %s zwróciła pełną stronę (%d) — starsze wiadomości mogły zostać "
                "ucięte przez sufit odczytu",
                chat_id, top,
            )
        return wiadomosci

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
