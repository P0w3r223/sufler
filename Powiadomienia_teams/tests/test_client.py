import json
import logging
from datetime import datetime, timezone

import httpx
import pytest

from powiadomienia_teams.graph.auth import AuthExpiredError
# 0.2.19 nie ma osobnej klasy `GraphResponseError` — brak wymaganego pola w odpowiedzi Graph
# jest zgłaszany gołym `RuntimeError`. Gwarancja („pusty wynik jest błędem, nie danymi") ta sama,
# typ słabszy: wołający nie odróżni tego od dowolnej innej awarii środowiska uruchomieniowego.
from powiadomienia_teams.graph.client import (
    _MAX_PAGES,
    GraphClient,
    GraphPermissionError,
    GraphTruncatedReadError,
)

UTC = timezone.utc


def _graph(handler) -> GraphClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    gc = GraphClient(http, lambda: "tok", sleep=lambda _s: None)
    gc.refresh_auth()
    return gc


def test_list_members_maps_and_paginates():
    pages = {
        "https://graph.microsoft.com/v1.0/teams/T/members": {
            "value": [{"userId": "u1", "displayName": "Ala", "roles": ["owner"]}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/teams/T/members?page=2",
        },
        "https://graph.microsoft.com/v1.0/teams/T/members?page=2": {
            "value": [{"userId": "u2", "displayName": "Bok"}],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json=pages[str(request.url)])

    members = _graph(handler).list_members("T")
    assert [m.user_id for m in members] == ["u1", "u2"]


def test_list_members_skips_entries_without_userid():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"value": [{"userId": "u1", "displayName": "Ala"}, {"displayName": "Bot"}]},
        )

    members = _graph(handler).list_members("T")
    assert [m.user_id for m in members] == ["u1"]


def test_read_shifts_filters_window():
    body = {
        "value": [
            {
                "userId": "u1",
                "sharedShift": {
                    "startDateTime": "2026-07-20T08:00:00Z",
                    "endDateTime": "2026-07-20T16:00:00Z",
                },
            },
            {  # poza oknem — pomijane
                "userId": "u1",
                "sharedShift": {
                    "startDateTime": "2026-07-10T08:00:00Z",
                    "endDateTime": "2026-07-10T16:00:00Z",
                },
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    out = _graph(handler).read_shifts(
        "T", datetime(2026, 7, 20, tzinfo=UTC), datetime(2026, 7, 27, tzinfo=UTC)
    )
    assert len(out) == 1
    assert out[0].start == datetime(2026, 7, 20, 8, tzinfo=UTC)


def test_get_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json={"id": "me-id"})

    assert _graph(handler).get_me() == "me-id"
    assert calls["n"] == 2


def test_create_or_get_chat_posts_oneonone_and_returns_id():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://graph.microsoft.com/v1.0/chats"
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "chat-123"})

    chat_id = _graph(handler).create_or_get_chat("me", "u1")
    assert chat_id == "chat-123"
    assert seen["body"]["chatType"] == "oneOnOne"
    assert len(seen["body"]["members"]) == 2


def test_send_chat_message_posts_html():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://graph.microsoft.com/v1.0/chats/chat-123/messages"
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "msg-1"})

    _graph(handler).send_chat_message("chat-123", "<p>hej</p>")
    assert seen["body"]["body"]["contentType"] == "html"
    assert seen["body"]["body"]["content"] == "<p>hej</p>"


def test_list_chat_messages_returns_value():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "chats/chat-1/messages" in str(request.url)
        return httpx.Response(200, json={"value": [{"id": "m1"}, {"id": "m2"}]})

    msgs = _graph(handler).list_chat_messages("chat-1")
    assert [m["id"] for m in msgs] == ["m1", "m2"]


def test_create_shift_posts_shared_shift():
    from powiadomienia_teams.domain.models import Shift

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://graph.microsoft.com/v1.0/teams/T/schedule/shifts"
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "shift-1"})

    shift = Shift(
        "u1",
        datetime(2026, 7, 20, 6, tzinfo=UTC),
        datetime(2026, 7, 20, 14, tzinfo=UTC),
        scheduling_group_id="TAG",
    )
    shift_id = _graph(handler).create_shift("T", shift)
    assert shift_id == "shift-1"
    assert seen["body"]["userId"] == "u1"
    assert seen["body"]["schedulingGroupId"] == "TAG"
    assert seen["body"]["sharedShift"]["startDateTime"] == "2026-07-20T06:00:00Z"
    assert seen["body"]["sharedShift"]["theme"] == "green"  # brak koloru → domyślnie stacjonarnie


def test_create_time_off_posts_shared_time_off():
    """Ścieżka ZAPISU czasu wolnego nie miała żadnego testu, w odróżnieniu od `create_shift`.

    To nieodwracalny zapis w grafiku klienta: literówka w `sharedTimeOff`/`timeOffReasonId` albo
    dryf kontraktu Graph wyszedłby dopiero w produkcji, u pracownika, po jego „tak".
    """
    from powiadomienia_teams.domain.models import TimeOff

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://graph.microsoft.com/v1.0/teams/T/schedule/timesOff"
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "timeoff-1"})

    time_off = TimeOff(
        "u1",
        datetime(2026, 7, 24, tzinfo=timezone.utc),
        datetime(2026, 7, 25, tzinfo=timezone.utc),
        reason_id="TOR_URLOP",
    )

    assert _graph(handler).create_time_off("T", time_off) == "timeoff-1"
    assert seen["body"]["userId"] == "u1"
    assert seen["body"]["sharedTimeOff"]["timeOffReasonId"] == "TOR_URLOP"
    assert seen["body"]["sharedTimeOff"]["startDateTime"] == "2026-07-24T00:00:00Z"
    assert seen["body"]["sharedTimeOff"]["endDateTime"] == "2026-07-25T00:00:00Z"


def test_list_time_off_reasons_pomija_nieaktywne_i_niepelne():
    """Nieaktywny powód dalej wraca z Graph — użycie go dałoby odrzucony zapis u pracownika."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "timeOffReasons" in str(request.url)
        return httpx.Response(
            200,
            json={
                "value": [
                    {"id": "TOR_URLOP", "displayName": "Urlop", "isActive": True},
                    {"id": "TOR_STARY", "displayName": "Dawny powód", "isActive": False},
                    {"id": "TOR_BEZ_NAZWY", "isActive": True},
                    {"displayName": "Bez id", "isActive": True},
                ]
            },
        )

    reasons = _graph(handler).list_time_off_reasons("T")

    assert reasons.by_name == {"urlop": "TOR_URLOP"}
    assert reasons.names == {"TOR_URLOP": "Urlop"}


def test_create_shift_includes_theme_when_set():
    from powiadomienia_teams.domain.models import Shift

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "shift-2"})

    shift = Shift(
        "u1",
        datetime(2026, 7, 20, 6, tzinfo=UTC),
        datetime(2026, 7, 20, 14, tzinfo=UTC),
        scheduling_group_id="TAG",
        theme="green",
    )
    _graph(handler).create_shift("T", shift)
    assert seen["body"]["sharedShift"]["theme"] == "green"


def test_get_raises_auth_expired_on_401():
    """401 mimo udanego cichego odświeżenia = token odrzucony przez Graph — usługa ma stanąć."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})

    with pytest.raises(AuthExpiredError, match="--login"):
        _graph(handler).list_members("T")


def test_post_raises_auth_expired_on_401():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})

    with pytest.raises(AuthExpiredError):
        _graph(handler).send_chat_message("chat-1", "<p>x</p>")


def test_403_raises_permission_error_with_body():
    """Ciało 403 to jedyne miejsce z przyczyną — musi trafić i do wyjątku, i do logu."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": "Forbidden",
                    "message": "Missing scope Schedule.ReadWrite.All",
                }
            },
        )

    with pytest.raises(GraphPermissionError, match="Schedule.ReadWrite.All"):
        _graph(handler).list_members("T")


def test_error_body_is_logged(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal boom")

    with caplog.at_level(logging.ERROR), pytest.raises(httpx.HTTPStatusError):
        _graph(handler).list_members("T")
    assert "Internal boom" in caplog.text


def test_5xx_still_raises_http_status_error():
    """Transientne 5xx zostaje zwykłym błędem HTTP — ponawianie wyżej ma sens."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="try later")

    with pytest.raises(httpx.HTTPStatusError):
        _graph(handler).list_members("T")


def test_read_time_off_uses_overlap_not_start():
    """Urlop zaczęty PRZED oknem i trwający w nim musi być widoczny — inaczej osoba w środku
    dwutygodniowego urlopu wyszłaby jako »bez grafiku« i dostałaby prośbę."""
    body = {
        "value": [
            {  # zaczyna się tydzień wcześniej, ale przecina okno docelowe
                "userId": "u1",
                "sharedTimeOff": {
                    "startDateTime": "2026-07-13T00:00:00Z",
                    "endDateTime": "2026-07-25T00:00:00Z",
                    "timeOffReasonId": "TOR_URLOP",
                },
            },
            {  # w całości po oknie — pomijany
                "userId": "u2",
                "sharedTimeOff": {
                    "startDateTime": "2026-08-01T00:00:00Z",
                    "endDateTime": "2026-08-05T00:00:00Z",
                    "timeOffReasonId": "TOR_URLOP",
                },
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/schedule/timesOff")
        return httpx.Response(200, json=body)

    out = _graph(handler).read_time_off(
        "T", datetime(2026, 7, 20, tzinfo=UTC), datetime(2026, 7, 27, tzinfo=UTC)
    )
    assert [t.user_id for t in out] == ["u1"]


def _graph_z_zapisem_snu(handler, spane: list):
    """Klient z atrapą snu — zapisuje, ile by przespał, zamiast spać.

    0.2.19 liczy budżet dławienia LICZNIKIEM sekund odjmowanym przy każdym czekaniu
    (`_MAX_RETRY_BUDGET_S`), a nie deadline'em zegara monotonicznego, więc atrapa zegara przestała
    być potrzebna: budżet wyczerpuje się od samych wartości `Retry-After`.
    """

    def spij(sekundy):
        spane.append(sekundy)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    gc = GraphClient(http, lambda: "tok", sleep=spij)
    gc.refresh_auth()
    return gc


def test_dlugi_retry_after_jest_honorowany_w_ramach_budzetu():
    """Ograniczamy SUMĘ czekania, nie pojedynczą przerwę.

    Sufit 60 s na próbę wyglądał ostrożnie, ale zamieniał „wolno, ale w końcu się uda"
    w „porzucone": przy `Retry-After: 3600` pięć prób wyczerpywało się w pięć minut i przebieg
    tygodniowy padał. Ignorowanie nagłówka bywa też karane wydłużeniem dławienia.
    """
    spane: list = []
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "3600"}, json={})
        return httpx.Response(200, json={"id": "me-id"})

    assert _graph_z_zapisem_snu(handler, spane).get_me() == "me-id"
    assert spane == [900], spane  # cały budżet w jednym oczekiwaniu, nie 60 s


def test_budzet_ponowien_nie_jest_nieskonczony():
    """Dławienie bez końca musi w końcu ustąpić błędem, a nie usypiać procesu na zawsze."""
    spane: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "300"}, json={})

    with pytest.raises(httpx.HTTPStatusError):
        _graph_z_zapisem_snu(handler, spane).get_me()
    assert sum(spane) <= 900  # łączne czekanie mieści się w budżecie


def test_przekroczony_limit_stron_konczy_sie_bledem_zamiast_niepelnej_listy():
    """Ucięte stronicowanie MUSI przewrócić przebieg, a nie zwrócić połowę grafiku.

    Wykrywanie luk („kto nie ma zmian") pracuje na tym, co wróciło, więc niepełny odczyt to
    prośby wysłane osobom, które grafik MAJĄ, a po ich „tak" DRUGI komplet wpisów w Shifts —
    zapis nieodwracalny. Sam log tego nie zatrzymywał: usługa bezobsługowa bez monitoringu
    logów zachowywała się dokładnie tak, jakby przebieg się udał.
    """
    zadania = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        zadania["n"] += 1
        return httpx.Response(
            200,
            json={
                "value": [{"userId": f"u{zadania['n']}", "displayName": "Ala"}],
                # Graph deklaruje kolejną stronę BEZ KOŃCA — tak wygląda kolekcja większa niż limit.
                "@odata.nextLink": f"https://graph.microsoft.com/v1.0/teams/T/members?p={zadania['n']}",
            },
        )

    with pytest.raises(GraphTruncatedReadError, match="NIEPEŁNY"):
        _graph(handler).list_members("T")
    assert zadania["n"] == _MAX_PAGES  # limit nadal chroni przed czytaniem w nieskończoność


def test_sufit_czasu_przebiegu_ogranicza_CALE_stronicowanie_a_nie_pojedyncze_zadanie():
    """Jeden sufit na całą operację — inaczej limit stron zamienia się w limit godzin.

    Budżet dławienia w 0.2.19 liczy się PER ŻĄDANIE (900 s), a `_get_all` dopuszcza 50 stron, więc
    sam klient pozwoliłby jednemu odczytowi kolekcji czekać ~12,5 h. Przez cały ten czas puls bije
    (klient śpi przez `spij_z_pulsem`), więc healthcheck orzekałby „zdrowy" dla usługi, która od
    godzin nic nie robi.

    Ograniczeniem jest `sprawdz_czas` — sufit CZASU PRZEBIEGU wstrzykiwany przy budowie klienta
    (`cli.zbuduj_zaleznosci` podaje `BudzetPrzebiegu.sprawdz`). Ten test świadczy o kontrakcie
    klienta wobec tego szwu, a nie o wewnętrznym liczniku: sprawdzenie wypada przed KAŻDYM
    żądaniem i przed KAŻDYM czekaniem, z długością planowanej przerwy, więc przerwanie następuje
    PRZED snem, a nie po nim.
    """
    spane: list = []
    pytania: list[float] = []
    dlawione: set[str] = set()

    class _OknoZamkniete(RuntimeError):
        pass

    def sprawdz(za_ile_s: float = 0.0) -> None:
        pytania.append(za_ile_s)
        if sum(spane) + za_ile_s > 900:  # tyle zostało do końca okna przebiegu
            raise _OknoZamkniete("okno przebiegu zamknęłoby się w tym czekaniu")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url not in dlawione:
            dlawione.add(url)  # KAŻDA strona jest raz dławiona długim Retry-After
            return httpx.Response(429, headers={"Retry-After": "300"}, json={})
        strona = len(dlawione)
        return httpx.Response(
            200,
            json={
                "value": [{"userId": f"u{strona}", "displayName": "Ala"}],
                "@odata.nextLink": f"https://graph.microsoft.com/v1.0/teams/T/members?p={strona}",
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    gc = GraphClient(
        http, lambda: "tok", sleep=lambda s: spane.append(s), sprawdz_czas=sprawdz
    )
    gc.refresh_auth()

    with pytest.raises(_OknoZamkniete):
        gc.list_members("T")

    assert sum(spane) <= 900, spane  # cała operacja, nie każda strona z osobna
    assert 300.0 in pytania  # sprawdzenie dostaje DŁUGOŚĆ planowanej przerwy, nie samo zero
    assert pytania[0] == 0.0  # i wypada też przed samym żądaniem


def test_get_me_bez_id_jest_bledem_a_nie_pustym_napisem():
    """Puste `me_id` rozbrajało DWA zabezpieczenia naraz i było zupełnie ciche.

    Bez tożsamości bota `run_once` nie odsieje konta bota z listy kandydatów (bot zagaduje sam
    siebie), a `incoming_after` nie rozpozna własnych wiadomości — czyli bierze własny nudge za
    odpowiedź pracownika i wchodzi w rozmowę ze sobą.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"displayName": "Virtual WorkMate"})  # bez `id`

    with pytest.raises(RuntimeError, match="id"):
        _graph(handler).get_me()


def test_create_or_get_chat_bez_id_jest_bledem():
    """Bez id czatu w stanie ląduje pending, którego nie da się już nigdy odczytać ani zagadać."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"chatType": "oneOnOne"})  # bez `id`

    with pytest.raises(RuntimeError, match="id czatu"):
        _graph(handler).create_or_get_chat("me", "u1")
