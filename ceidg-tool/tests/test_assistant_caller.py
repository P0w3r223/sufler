"""`assistant/caller.py` — jedyny moduł znający SDK (ADR-0011, decyzja 2 i 8).

Cały plik działa **bez sieci i bez klucza z prawdziwego świata**: SDK dostaje atrapę transportu
`httpx2.MockTransport`, więc sprawdzamy prawdziwy kod klienta, a nie jego podmienioną wersję.
To ta sama zasada, którą wymusiła faza 3f: szew testowy jadący inną ścieżką niż produkcja
ukrywa zachowanie produkcji.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx2
import pytest

from ceidg_tool.assistant.caller import (
    AnthropicCaller,
    AssistantUnavailableError,
    ModelRefusedError,
)
from ceidg_tool.assistant.prompt import build_system
from ceidg_tool.assistant.schema import OgraniczenieKod
from ceidg_tool.errors import UntrustedLinkError
from ceidg_tool.httpclient import build_model_http_client

# Słownik zastępczy: pięć kodów wystarczy do każdej własności, której pilnują te testy, a pełny
# plik ma własny zestaw sprawdzeń (`test_assistant_pkd_data.py`), bo tam pytanie brzmi inaczej —
# nie „czy kod działa", tylko „czy zawartość zgadza się z klasyfikacją".
#
# Kody i nazwy są **prawdziwe i z PKD 2025**, przypięte do wygenerowanego słownika przez
# `test_assistant_pkd_data.py`. Do audytu 2026-09-07 stały tu kody z PKD 2007 (`3030Z`, `4120Z`,
# `6201Z` — trzy z pięciu nie istnieją w 2025) z nazwami przepisanymi dosłownie z rocznika 2007,
# a moduł pod testem ma na sztywno `PKD_VINTAGE = "PKD 2025"` i wstrzykuje ten napis do promptu.
# To jest dokładnie ta awaria, przez którą wybrano zły rocznik klasyfikacji: ręcznie napisana
# atrapa opisująca rzeczywistość, której nie ma. `6201Z` był w niej kodem, który `CLAUDE.md`
# wymienia z nazwiska jako godło tego defektu.
SLOWNIK = {
    "0111Z": "Uprawa zbóż innych niż ryż, roślin strączkowych i roślin oleistych na nasiona",
    "3031Z": "Produkcja cywilnych statków powietrznych, statków kosmicznych i podobnych maszyn",
    "4100A": "Roboty budowlane związane ze wznoszeniem budynków mieszkalnych",
    "4711Z": (
        "Sprzedaż detaliczna niewyspecjalizowana z przewagą żywności, napojów lub wyrobów "
        "tytoniowych"
    ),
    "6210B": "Pozostała działalność w zakresie programowania",
}

DOBRA_ODPOWIEDZ = {
    "wojewodztwo": ["podlaskie"],
    "powiat": [],
    "gmina": [],
    "miasto": ["Białystok"],
    "ulica": [],
    "kod": [],
    "nazwa": [],
    "imie": [],
    "nazwisko": [],
    "nip": [],
    "regon": [],
    "pkd": ["4100A"],
    "status": ["AKTYWNY"],
    "data_od": "2025-01-01",
    "data_do": "2025-12-31",
    "szczegoly": False,
    "ograniczenia": ["SPOLKI_W_KRS"],
}


def sse(tresc: str, stop_reason: str = "end_turn") -> bytes:
    """Minimalny strumień zdarzeń Messages API — tyle, ile czyta `stream.text_stream`."""
    zdarzenia: list[tuple[str, dict[str, Any]]] = [
        (
            "message_start",
            {
                "message": {
                    "id": "msg",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-opus-5",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                }
            },
        ),
        ("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
    ]
    for kawalek in (tresc[i : i + 20] for i in range(0, len(tresc), 20)):
        zdarzenia.append(
            ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": kawalek}})
        )
    zdarzenia += [
        ("content_block_stop", {"index": 0}),
        (
            "message_delta",
            {
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": 20},
            },
        ),
        ("message_stop", {}),
    ]
    body = ""
    for nazwa, dane in zdarzenia:
        body += f"event: {nazwa}\ndata: {json.dumps({'type': nazwa, **dane})}\n\n"
    return body.encode("utf-8")


class Zapis:
    """Odbiorca `Events`, który zapamiętuje wyłącznie to, o co pytają testy."""

    def __init__(self) -> None:
        self.model: list[tuple[float, int]] = []
        self.czekania: list[tuple[float, str]] = []

    def on_request(self, endpoint: str, status: int, elapsed_s: float) -> None: ...
    def on_wait(self, seconds: float, reason: str, resume_at_epoch: float) -> None:
        self.czekania.append((seconds, reason))

    def on_page(self, page_index: int, records: int, total: int | None) -> None: ...
    def on_details(self, done: int, total: int) -> None: ...
    def on_export(self, done: int, total: int) -> None: ...
    def on_download(self, done_bytes: int, total_bytes: int | None) -> None: ...
    def on_model(self, elapsed_s: float, tokens: int) -> None:
        self.model.append((elapsed_s, tokens))

    def on_message(self, text: str) -> None: ...
    def close(self) -> None: ...


def caller(
    odpowiedzi: list[Any], events: Zapis | None = None
) -> tuple[AnthropicCaller, list[httpx2.Request]]:
    """`AnthropicCaller` nad atrapą transportu. `odpowiedzi` konsumowane po kolei."""
    zapytania: list[httpx2.Request] = []
    kolejka = list(odpowiedzi)

    def handler(request: httpx2.Request) -> httpx2.Response:
        request.read()
        zapytania.append(request)
        nastepna = kolejka.pop(0)
        if isinstance(nastepna, int):
            return httpx2.Response(nastepna, json={"type": "error", "error": {"message": "x"}})
        # `bytes` to gotowy strumień SSE (np. z blokiem myślenia), `str` to sama treść odpowiedzi.
        cialo = nastepna if isinstance(nastepna, bytes) else sse(nastepna)
        return httpx2.Response(200, content=cialo, headers={"content-type": "text/event-stream"})

    klient = build_model_http_client(transport=httpx2.MockTransport(handler))
    return (
        AnthropicCaller(api_key="sk-ant-test", slownik=SLOWNIK, http_client=klient, events=events),
        zapytania,
    )


# --- brak klucza --------------------------------------------------------------------------


def test_without_a_key_the_assistant_refuses_with_a_sentence() -> None:
    """Brak klucza nie jest wyjątkiem programisty — to stan, o którym trzeba powiedzieć."""
    with pytest.raises(AssistantUnavailableError, match="sprawdz-token"):
        AnthropicCaller(api_key="", slownik=SLOWNIK)


# --- ścieżka szczęśliwa -------------------------------------------------------------------


def test_a_sentence_becomes_criteria_in_one_request() -> None:
    zapis = Zapis()
    rozmowa, zapytania = caller([json.dumps(DOBRA_ODPOWIEDZ)], zapis)

    wynik = rozmowa.interpret("firmy budowlane w Białymstoku", dzisiaj=date(2026, 9, 7))

    assert len(zapytania) == 1
    assert wynik.kryteria.miasto == ("Białystok",)
    assert wynik.kryteria.pkd == ("4100A",)
    assert wynik.kody_pkd == (("4100A", SLOWNIK["4100A"]),)
    assert wynik.ograniczenia == (OgraniczenieKod.SPOLKI_W_KRS,)


def test_the_request_carries_only_the_dictionary_and_the_question() -> None:
    """§B po stronie bajtów: do modelu idzie pytanie i słownik, pobrane rekordy nigdy.

    Test patrzy na **wysłane ciało żądania**, a nie na intencję kodu — uzupełnia regułę granic
    13, która mówi, że rekordy nie mają którędy tu trafić.
    """
    rozmowa, zapytania = caller([json.dumps(DOBRA_ODPOWIEDZ)])
    rozmowa.interpret("firmy w Łomży", dzisiaj=date(2026, 9, 7))

    cialo = json.loads(zapytania[0].content)
    assert [m["role"] for m in cialo["messages"]] == ["user"]
    assert cialo["messages"][0]["content"] == (
        "Dzisiejsza data: 2026-09-07.\nZdanie użytkownika: firmy w Łomży"
    )
    # Blok systemowy przypięty do wyniku `build_system`, a nie do dwóch podciągów: inaczej
    # cokolwiek dopisanego do promptu przechodziłoby niezauważone.
    system = cialo["system"][0]["text"]
    assert system == build_system(SLOWNIK)
    # Kluczowa asercja jest **strukturalna**, a nie łowieniem słów: nazwy pól (`nip`, `regon`)
    # siedzą w schemacie i muszą tam być, więc szukanie ich jako „wycieku" dawało fałszywy alarm
    # — złapał go ten test na sobie samym. Żądanie ma się składać z części, które znamy.
    # `metadata` **nie** jest tu dozwolone: to udokumentowane pole Messages API, które potrafi
    # nieść identyfikator podany przez wywołującego. Nic go dziś nie wysyła i tak ma zostać.
    znane = {"model", "max_tokens", "system", "messages", "output_config", "stream"}
    assert set(cialo) <= znane, f"nieznana część żądania: {sorted(set(cialo) - znane)}"
    assert cialo["system"] == [
        {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
    ]

    # Do tego kontrola wprost na wartościach, które nigdy nie mają prawa tam trafić.
    surowe = zapytania[0].content.decode("utf-8")
    for zakazane in ("sk-ant-test", "Bearer", "9876543210", ".sqlite", "AppData"):
        assert zakazane not in surowe, f"do modelu poszło {zakazane!r}"


def test_the_system_block_is_cached_and_the_question_is_not() -> None:
    """Prefiks nosi `cache_control`, tura użytkownika nie — inaczej cache nigdy nie odczyta."""
    rozmowa, zapytania = caller([json.dumps(DOBRA_ODPOWIEDZ)])
    rozmowa.interpret("cokolwiek", dzisiaj=date(2026, 9, 7))

    cialo = json.loads(zapytania[0].content)
    assert cialo["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in cialo["messages"][0]


def test_progress_is_reported_from_before_the_first_token() -> None:
    """Kilkanaście sekund nad 25 tys. tokenów promptu to cisza, którą trzeba przerwać."""
    zapis = Zapis()
    rozmowa, _ = caller([json.dumps(DOBRA_ODPOWIEDZ)], zapis)

    rozmowa.interpret("firmy budowlane", dzisiaj=date(2026, 9, 7))

    assert zapis.model[0] == (0.0, 0), "pierwsze zgłoszenie ma paść przed pierwszym tokenem"
    assert len(zapis.model) > 2
    assert [t for _, t in zapis.model] == sorted(t for _, t in zapis.model)


# --- naprawa i odmowy ---------------------------------------------------------------------


def test_a_malformed_answer_gets_exactly_one_repair_attempt() -> None:
    """Jedna próba naprawy nad tym samym prefiksem — i koniec.

    Ograniczenie do dwóch żądań na próbę jest tym, co pozwala tabeli kosztów mówić prawdę.
    """
    zla = json.dumps({**DOBRA_ODPOWIEDZ, "pkd": ["9999Z"]})
    rozmowa, zapytania = caller([zla, json.dumps(DOBRA_ODPOWIEDZ)])

    wynik = rozmowa.interpret("firmy budowlane", dzisiaj=date(2026, 9, 7))

    assert len(zapytania) == 2
    assert wynik.kryteria.pkd == ("4100A",)
    naprawa = json.loads(zapytania[1].content)
    assert [m["role"] for m in naprawa["messages"]] == ["user", "assistant", "user"]
    assert "9999Z" in naprawa["messages"][2]["content"]


def test_two_bad_answers_end_in_a_refusal_not_a_third_request() -> None:
    zla = json.dumps({**DOBRA_ODPOWIEDZ, "pkd": ["9999Z"]})
    rozmowa, zapytania = caller([zla, zla])

    with pytest.raises(ModelRefusedError, match="9999Z"):
        rozmowa.interpret("firmy budowlane", dzisiaj=date(2026, 9, 7))

    assert len(zapytania) == 2


def test_answer_that_is_not_json_is_refused_by_name() -> None:
    rozmowa, _ = caller(["to nie jest JSON", "nadal nie"])

    with pytest.raises(ModelRefusedError, match="JSON"):
        rozmowa.interpret("cokolwiek", dzisiaj=date(2026, 9, 7))


def test_a_field_outside_the_schema_is_refused() -> None:
    """`extra="forbid"` działa także na odpowiedź modelu, nie tylko na testy schematu."""
    przemycone = json.dumps({**DOBRA_ODPOWIEDZ, "max_rekordow": 50})
    rozmowa, _ = caller([przemycone, przemycone])

    with pytest.raises(ModelRefusedError, match="max_rekordow|schemat"):
        rozmowa.interpret("kilka firm budowlanych", dzisiaj=date(2026, 9, 7))


def test_a_rate_limit_is_announced_and_retried_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pauza jest **zapowiadana**, a nie przemilczana wewnątrz SDK (dlatego `max_retries=0`)."""
    monkeypatch.setattr("ceidg_tool.assistant.caller.time.sleep", lambda _: None)
    zapis = Zapis()
    rozmowa, zapytania = caller([429, json.dumps(DOBRA_ODPOWIEDZ)], zapis)

    wynik = rozmowa.interpret("firmy budowlane", dzisiaj=date(2026, 9, 7))

    assert len(zapytania) == 2
    assert wynik.kryteria.pkd == ("4100A",)
    assert zapis.czekania and zapis.czekania[0][1] == "model"


def test_a_rejected_key_says_what_to_do(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ceidg_tool.assistant.caller.time.sleep", lambda _: None)
    rozmowa, zapytania = caller([401])

    with pytest.raises(AssistantUnavailableError, match="sprawdz-token"):
        rozmowa.interpret("cokolwiek", dzisiaj=date(2026, 9, 7))

    assert len(zapytania) == 1, "401 nie jest do ponowienia"


# --- bramka wyjścia -----------------------------------------------------------------------


def test_the_model_client_refuses_the_ceidg_host() -> None:
    """Dwie listy hostów, nigdy sumowane: klient modelu nie sięgnie do rejestru."""
    with build_model_http_client(
        transport=httpx2.MockTransport(lambda r: httpx2.Response(200))
    ) as c:
        with pytest.raises(UntrustedLinkError):
            c.get("https://dane.biznes.gov.pl/api/ceidg/v3/firmy")


# --- budżet, myślenie i taksonomia błędów (uwagi z przeglądu 2026-09-07) ------------------


def sse_z_myśleniem(tresc: str) -> bytes:
    """Strumień z blokiem myślenia przed tekstem — tak wygląda odpowiedź Opus 5.

    Myślenie jest domyślnie włączone i **nie niesie tekstu**, więc bez tego przypadku test
    postępu przechodził dlatego, że atrapa oddawała same `text_delta`. Faza myślenia to
    większość opóźnienia; jeśli w niej nic nie mruga, operator widzi zawieszenie.
    """
    surowy = sse(tresc).decode("utf-8")
    mysli = ""
    for i, kawalek in enumerate(("Analizuję ", "zdanie ", "operatora.")):
        if i == 0:
            mysli += 'event: content_block_start\ndata: {"type": "content_block_start",'
            mysli += ' "index": 0, "content_block": {"type": "thinking", "thinking": ""}}\n\n'
        mysli += 'event: content_block_delta\ndata: {"type": "content_block_delta", "index": 0,'
        mysli += f' "delta": {{"type": "thinking_delta", "thinking": "{kawalek}"}}}}\n\n'
    mysli += 'event: content_block_stop\ndata: {"type": "content_block_stop", "index": 0}\n\n'
    start, reszta = surowy.split("event: content_block_start", 1)
    return (start + mysli + "event: content_block_start" + reszta).encode("utf-8")


def test_progress_moves_during_the_thinking_phase() -> None:
    """Zdarzenia myślenia też są oznaką życia — i to one trwają najdłużej."""
    zapis = Zapis()
    rozmowa, _ = caller([sse_z_myśleniem(json.dumps(DOBRA_ODPOWIEDZ))], zapis)

    rozmowa.interpret("firmy budowlane", dzisiaj=date(2026, 9, 7))

    # Do 2026-09-07 liczyły się wyłącznie znaki `text_delta`, więc przez całą fazę myślenia
    # zgłoszenia padały z **niezmienną** wartością 0 — na ekranie nie do odróżnienia od zwisu,
    # co znalazł właściciel przy bramce 3. Sam fakt zgłoszeń nie wystarcza więc jako asercja:
    # sprawdzamy, że raportowana liczba **rośnie**, i to zanim pojawi się pierwszy tekst.
    wartosci = [t for _, t in zapis.model]
    assert len(set(wartosci)) > 3, f"licznik stoi w miejscu: {zapis.model}"
    assert wartosci == sorted(wartosci), "licznik nie może się cofać"
    # Trzy kawałki myślenia z atrapy dają wzrost jeszcze przed JSON-em; gdyby liczył się sam
    # tekst, pierwsze niezerowe zgłoszenie padłoby dopiero razem z odpowiedzią.
    assert any(t > 0 for t in wartosci[:6]), f"cisza w fazie myślenia: {zapis.model}"


def test_a_retry_and_a_repair_cannot_add_up_to_four_requests() -> None:
    """Budżet jest wspólny dla ponowień i naprawy — inaczej „dwa żądania" było nieprawdą.

    Wcześniej `_ask` ponawiał raz, a `interpret` wołał go dwa razy, więc najgorszy przypadek
    to 2 × 2 = 4 — przy docstringu obiecującym dwa i tabeli kosztów opartej na tej liczbie.
    Dwa testy dowodziły po jednym czynniku osobno i żaden nie widział iloczynu.
    """
    zla = json.dumps({**DOBRA_ODPOWIEDZ, "pkd": ["9999Z"]})
    rozmowa, zapytania = caller([500, zla, zla, zla])

    with pytest.raises((ModelRefusedError, AssistantUnavailableError)):
        rozmowa.interpret("firmy budowlane", dzisiaj=date(2026, 9, 7))

    assert len(zapytania) <= 2, f"wydano {len(zapytania)} żądań, budżet to 2"


def test_a_truncated_answer_is_named_as_such_not_as_broken_json() -> None:
    """`stop_reason=max_tokens` ma własny komunikat — inaczej myli co do przyczyny.

    Tokeny myślenia liczą się do `max_tokens`, więc zbyt niski sufit ucinał odpowiedź przed
    JSON-em, a operator czytał „to nie jest poprawny JSON" i szukał błędu u siebie.
    """
    ucieta = sse(json.dumps(DOBRA_ODPOWIEDZ), stop_reason="max_tokens")
    rozmowa, _ = caller([ucieta, ucieta])

    with pytest.raises(ModelRefusedError, match="ucięta"):
        rozmowa.interpret("firmy budowlane", dzisiaj=date(2026, 9, 7))


def test_a_refused_host_is_reported_as_a_refusal_not_as_no_connection() -> None:
    """SDK zawija wyjątki transportu w `APIConnectionError` — nasza odmowa też się w nim gubiła.

    Skutek był bezpieczny (odmowa działała), ale komunikat mylił dokładnie wtedy, gdy trzeba go
    zrozumieć, i ponawiał host, który nigdy nie będzie dozwolony.
    """
    klient = build_model_http_client(
        transport=httpx2.MockTransport(lambda r: httpx2.Response(200)),
        allowed=frozenset({"nikt.example.test"}),
    )
    rozmowa = AnthropicCaller(
        api_key="sk-ant-test-" + "a" * 20, slownik=SLOWNIK, http_client=klient
    )

    with pytest.raises(UntrustedLinkError):
        rozmowa.interpret("cokolwiek", dzisiaj=date(2026, 9, 7))
