"""Bezpieczeństwo: token z nagłówka ``Authorization`` nie wycieka do logu ani do tracebacku.

Jedyny punkt Fazy 0 planu, który do tej pory stał **na słowo grepa**: „do sprawdzenia SONDĄ, nie
grepem — czy ``graph_http``/``jira_http`` nie wciągają nagłówka ``Authorization`` do tracebacku
``httpx``". Grep tego nie rozstrzyga, bo nikt tego nagłówka nie loguje wprost: pytanie brzmi, czy
zrobi to za nas biblioteka, składając komunikat wyjątku z obiektu żądania.

Wektor jest realny, bo oba transporty są w ścieżce, w której wyjątek WYPŁYWA: ``request_with_retry``
przepuszcza błąd transportu, gdy ponawianie jest wyłączone (żądania nieidempotentne) albo gdy próby
się wyczerpią. Ten wyjątek trafia do ``logger.exception`` w adapterach wyżej, a stamtąd do dziennika
kontenera — czytelnego dla każdego, kto ma dostęp do hosta.

Mierzymy TRZY drogi, którymi wartość mogłaby się wydostać: komunikat wyjątku, jego ``repr``
i pełny sformatowany traceback (ten ostatni niesie też lokalne zmienne ramek biblioteki, gdy
formatuje go coś z ``--show-locals``; tu sprawdzamy postać standardową).

**Co która sonda naprawdę bada.** Przy błędach TRANSPORTU treść komunikatu pochodzi z tego pliku
(wyjątek podnosi atrapa), więc badaną hipotezą nie jest „czy httpx wpisze token w komunikat", tylko
**czy związanie wyjątku z żądaniem** (``httpx._exceptions.request_context``) nie wypycha nagłówków
przez ``str``/``repr``/traceback. Komunikat składany przez samą bibliotekę bada dopiero sonda
``raise_for_status`` — i to ona niesie tu najwięcej.

**Każda sonda najpierw potwierdza, że BYŁO CO wynieść.** Bez tego przechodziłaby także wtedy, gdy
żądanie w ogóle nie niesie ``Authorization`` — a przy przepięciu uwierzytelniania (Jira na
``httpx.BasicAuth``, inne miejsce składania nagłówka w Grafie) zgniłaby bezobjawowo. To ten sam
wzorzec anty-gnilny, co ``_ZNANE_SEKRETY`` w ``test_secret_leakage.py``.
"""

from __future__ import annotations

import asyncio
import traceback

import httpx
import pytest

from sufler.adapters.outbound import graph_http, jira_http

_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.SEKRETNA-WARTOSC-TOKENU.podpis"
_NAGLOWKI = {"Authorization": f"Bearer {_TOKEN}", "Content-Type": "application/json"}


def _klient_ktory_pada(
    wyjatek: Exception,
    widziane: list[httpx.Headers],
    *,
    naglowki_klienta: bool = False,
) -> httpx.Client:
    """Klient httpx, w którym transport zawsze pada — z PRAWDZIWYM obiektem żądania.

    Atrapa podnosząca wyjątek „obok" żądania nie mierzyłaby niczego: cała hipoteza dotyczy tego,
    co biblioteka wciąga do wyjątku Z ŻĄDANIA. Dlatego wyjątek rodzi się w transporcie, któremu
    httpx podał już złożony ``Request`` — z nagłówkami.

    ``widziane`` zbiera nagłówki, które transport faktycznie dostał — sonda pyta o nie ZANIM
    sprawdzi wyciek, żeby odróżnić „nie wyciekło" od „nie było czego".
    """

    def handler(request: httpx.Request) -> httpx.Response:
        widziane.append(request.headers)
        raise type(wyjatek)(str(wyjatek), request=request)

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        headers=_NAGLOWKI if naglowki_klienta else None,
    )


def _bylo_co_wyniesc(widziane: list[httpx.Headers]) -> None:
    """Żądanie, które dotarło do transportu, MUSI nieść token — inaczej sonda mierzy pustkę."""
    assert widziane, "żądanie nie dotarło do transportu"
    assert _TOKEN in widziane[0].get("authorization", "")


def _teksty_bledu(exc: BaseException) -> list[str]:
    return [
        str(exc),
        repr(exc),
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    ]


def test_blad_transportu_w_graph_nie_niesie_tokenu():
    widziane: list[httpx.Headers] = []
    with (
        _klient_ktory_pada(httpx.ConnectError("połączenie odrzucone"), widziane) as klient,
        pytest.raises(httpx.TransportError) as zlapany,
    ):
        graph_http.request_with_retry(
            klient, "POST", "https://graph.microsoft.com/v1.0/me", headers=_NAGLOWKI
        )

    _bylo_co_wyniesc(widziane)
    for tekst in _teksty_bledu(zlapany.value):
        assert _TOKEN not in tekst


def test_blad_transportu_w_jira_nie_niesie_tokenu():
    """Jira nosi token w nagłówkach KLIENTA, nie per żądanie — inaczej niż Graph. Dla tej sondy
    to bez różnicy: httpx scala jedne z drugimi, zanim złoży ``Request``, więc obiekt, z którego
    biblioteka mogłaby zbudować komunikat, ma tę wartość tak samo."""
    widziane: list[httpx.Headers] = []
    with (
        _klient_ktory_pada(
            httpx.ReadTimeout("przekroczono czas odczytu"), widziane, naglowki_klienta=True
        ) as klient,
        pytest.raises(httpx.TransportError) as zlapany,
    ):
        jira_http.request_with_retry(
            klient, "GET", "https://przyklad.atlassian.net/rest/api/3/myself"
        )

    _bylo_co_wyniesc(widziane)
    for tekst in _teksty_bledu(zlapany.value):
        assert _TOKEN not in tekst


def test_blad_statusu_http_nie_niesie_tokenu():
    """Druga droga: ``raise_for_status`` składa komunikat Z ŻĄDANIA, więc to on jest kandydatem
    na wciągnięcie nagłówków — a wołają go adaptery nad oboma transportami."""

    widziane: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        widziane.append(request.headers)
        return httpx.Response(403, request=request, json={"error": "Forbidden"})

    # ``raise_for_status`` woła sam transport, więc sonda idzie DOKŁADNIE tą drogą, którą
    # idzie produkcja — nie własną rekonstrukcją komunikatu.
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as klient,
        pytest.raises(httpx.HTTPStatusError) as zlapany,
    ):
        graph_http.request_with_retry(
            klient, "GET", "https://graph.microsoft.com/v1.0/me", headers=_NAGLOWKI
        )

    _bylo_co_wyniesc(widziane)
    for tekst in _teksty_bledu(zlapany.value):
        assert _TOKEN not in tekst


def test_sonda_umie_zawiesc():
    """Kontrola samej sondy: gdyby wartość tokenu JEDNAK trafiła do komunikatu, asercje muszą to
    zobaczyć. Bez tego przebiegu trzy testy wyżej przechodziłyby także wtedy, gdyby porównywały
    coś, czego w tekstach nigdy nie ma."""
    udawany = httpx.ConnectError(f"nie udało się wysłać z nagłówkiem Bearer {_TOKEN}")

    assert any(_TOKEN in tekst for tekst in _teksty_bledu(udawany))


def test_asynchroniczny_transport_grafu_tez_nie_niesie_tokenu():
    """Wariant async obsługuje notifier Teams, który nosi token w nagłówkach KLIENTA — czyli
    inaczej niż przebadany Graph synchroniczny. Maszyneria wyjątków httpx jest ta sama, więc to
    domknięcie DEKLARACJI („transporty Graph i Jira"), nie nowa hipoteza."""
    widziane: list[httpx.Headers] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        widziane.append(request.headers)
        raise httpx.ConnectError("połączenie odrzucone", request=request)

    async def przebieg() -> BaseException:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), headers=_NAGLOWKI
        ) as klient:
            try:
                await graph_http.async_request_with_retry(
                    klient, "POST", "https://graph.microsoft.com/v1.0/teams", json={}
                )
            except httpx.TransportError as exc:
                return exc
        raise AssertionError("transport miał paść")

    zlapany = asyncio.run(przebieg())

    _bylo_co_wyniesc(widziane)
    for tekst in _teksty_bledu(zlapany):
        assert _TOKEN not in tekst
