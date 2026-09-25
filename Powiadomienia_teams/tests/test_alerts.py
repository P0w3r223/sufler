"""Alerty muszą być best-effort: alert, który kładzie usługę, jest gorszy niż brak alertu."""

import logging

import httpx

from powiadomienia_teams import alerts


class _Transport:
    """Atrapa klienta httpx — zapisuje żądania, opcjonalnie zawodzi albo przekierowuje."""

    def __init__(
        self,
        status: int = 200,
        boom: Exception | None = None,
        przekierowania: dict[str, tuple[int, str]] | None = None,
    ) -> None:
        self.status = status
        self.boom = boom
        self.przekierowania = przekierowania or {}
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict, **kwargs) -> httpx.Response:
        self.calls.append((url, json))
        self.kwargs = kwargs
        if self.boom is not None:
            raise self.boom
        if url in self.przekierowania:
            kod, cel = self.przekierowania[url]
            return httpx.Response(
                kod, headers={"location": cel}, request=httpx.Request("POST", url)
            )
        return httpx.Response(self.status, request=httpx.Request("POST", url))


def test_brak_url_nie_generuje_ruchu():
    """Pusty webhook = świadoma rezygnacja z alertowania, nie błąd."""
    transport = _Transport()
    assert alerts.send_alert("", "Tytuł", "Treść", client=transport) is False
    assert transport.calls == []


def test_wysylka_zawiera_tytul_i_tresc():
    transport = _Transport()
    assert (
        alerts.send_alert(
            "https://przyklad/hook",
            "Utracono sesję",
            "AADSTS50173",
            waga=alerts.KRYTYCZNY,
            client=transport,
        )
        is True
    )
    url, payload = transport.calls[0]
    assert url == "https://przyklad/hook"
    assert payload["tytul"] == "Utracono sesję"
    assert "AADSTS50173" in payload["text"]
    assert payload["waga"] == alerts.KRYTYCZNY


def test_wyjatek_sieciowy_nie_propaguje():
    """KLUCZOWE: awaria webhooka nie może przewrócić usługi, którą właśnie alarmuje."""
    transport = _Transport(boom=httpx.ConnectError("brak DNS"))
    assert alerts.send_alert("https://przyklad/hook", "T", "T", client=transport) is False


def test_odrzucenie_przez_serwer_nie_propaguje():
    transport = _Transport(status=500)
    assert alerts.send_alert("https://przyklad/hook", "T", "T", client=transport) is False


def test_url_nie_trafia_do_logu(caplog):
    """URL webhooka bywa sekretem (token w ścieżce) — nie może wyciec do logu przy błędzie."""
    sekret = "https://przyklad/hook?token=SEKRETNA-WARTOSC"
    transport = _Transport(status=403)
    with caplog.at_level("WARNING"):
        alerts.send_alert(sekret, "T", "T", client=transport)
    assert "SEKRETNA-WARTOSC" not in caplog.text


def test_przekierowanie_nie_jest_uznawane_za_sukces():
    """3xx oznacza, że alert NIE dotarł — a cichy zanik tego kanału jest najgorszą awarią modułu.

    httpx domyślnie nie podąża za przekierowaniem, a webhooki potrafią je zwracać (http→https,
    zmiana adresu Power Automate, reverse proxy). Wcześniej warunek błędu zaczynał się od 400,
    więc 301/302 raportowało sukces, którego nikt nigdy nie zobaczył.
    """
    for kod in (301, 302, 307, 308):
        transport = _Transport(status=kod)
        assert alerts.send_alert("https://przyklad/hook", "T", "T", client=transport) is False, kod


def test_przekierowanie_w_obrebie_hosta_jest_realizowane_POST_em():
    """Zmiana ścieżki Power Automate / reverse proxy: alert ma dotrzeć pod nowy adres."""
    transport = _Transport(
        przekierowania={"https://przyklad/hook": (308, "/nowy-hook")},
    )
    assert alerts.send_alert("https://przyklad/hook", "T", "T", client=transport) is True
    assert [u for u, _ in transport.calls] == [
        "https://przyklad/hook",
        "https://przyklad/nowy-hook",
    ]
    assert transport.calls[1][1]["tytul"] == "T"
    # httpx NIE podąża sam — inaczej kontrola hosta niżej byłaby fikcją.
    assert transport.kwargs.get("follow_redirects") is False


def test_przekierowanie_na_OBCY_host_nie_wynosi_tresci_alertu(caplog):
    """Audyt 2026-09-08, pkt 4: 307/308 na inny host powtarzało POST z pełną treścią alertu."""
    for cel in ("https://obcy.example/zbieracz", "http://przyklad/hook-bez-tls"):
        transport = _Transport(przekierowania={"https://przyklad/hook": (307, cel)})
        with caplog.at_level("WARNING"):
            ok = alerts.send_alert(
                "https://przyklad/hook", "T", "T", waga=alerts.INFO, client=transport
            )
        assert ok is False
        assert [u for u, _ in transport.calls] == ["https://przyklad/hook"], cel
    assert "ALERT_WEBHOOK_URL" in caplog.text


def test_petla_przekierowan_konczy_sie_niepowodzeniem():
    transport = _Transport(
        przekierowania={
            "https://przyklad/a": (302, "/b"),
            "https://przyklad/b": (302, "/a"),
        }
    )
    wynik = alerts.send_alert("https://przyklad/a", "T", "T", waga=alerts.INFO, client=transport)
    assert wynik is False
    assert len(transport.calls) == 4  # pierwsza próba + trzy przekierowania


def test_adres_webhooka_nie_trafia_do_logu_httpx(caplog):
    """Znana usterka z 0.2.19: httpx logował na INFO pełny URL webhooka razem z tokenem."""
    sekret = "https://discord.com/api/webhooks/123/SEKRETNY-TOKEN/slack"
    httpx_logger = logging.getLogger("httpx")
    alerts.ukryj_adres_w_logach(sekret)
    alerts.ukryj_adres_w_logach(sekret)  # wielokrotne wywołanie nie dubluje filtra
    try:
        filtry = [f for f in httpx_logger.filters if isinstance(f, alerts._UkryjAdresWebhooka)]
        assert len(filtry) == 1
        with caplog.at_level("INFO", logger="httpx"):
            httpx_logger.info('HTTP Request: %s %s "%s"', "POST", sekret, "HTTP/1.1 200 OK")
            httpx_logger.info(
                'HTTP Request: %s %s "%s"',
                "GET",
                "https://graph.microsoft.com/v1.0/me",
                "HTTP/1.1 200 OK",
            )
        assert "SEKRETNY-TOKEN" not in caplog.text
        assert "https://discord.com/<ukryte>" in caplog.text
        # Żądania do Graph zostają w logu w całości — to jedyna diagnostyka dławienia.
        assert "https://graph.microsoft.com/v1.0/me" in caplog.text
    finally:
        for f in filtry:
            httpx_logger.removeFilter(f)
