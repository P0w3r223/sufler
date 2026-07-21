"""Alerty muszą być best-effort: alert, który kładzie usługę, jest gorszy niż brak alertu."""
import httpx

from powiadomienia_teams import alerts


class _Transport:
    """Atrapa klienta httpx — zapisuje żądania, opcjonalnie zawodzi."""

    def __init__(self, status: int = 200, boom: Exception | None = None) -> None:
        self.status = status
        self.boom = boom
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict, **kwargs) -> httpx.Response:
        self.calls.append((url, json))
        self.kwargs = kwargs
        if self.boom is not None:
            raise self.boom
        return httpx.Response(self.status, request=httpx.Request("POST", url))


def test_brak_url_nie_generuje_ruchu():
    """Pusty webhook = świadoma rezygnacja z alertowania, nie błąd."""
    transport = _Transport()
    assert alerts.send_alert("", "Tytuł", "Treść", client=transport) is False
    assert transport.calls == []


def test_wysylka_zawiera_tytul_i_tresc():
    transport = _Transport()
    assert alerts.send_alert("https://przyklad/hook", "Utracono sesję", "AADSTS50173",
                             waga=alerts.KRYTYCZNY, client=transport) is True
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


def test_podazamy_za_przekierowaniem():
    transport = _Transport()
    alerts.send_alert("https://przyklad/hook", "T", "T", client=transport)
    assert transport.kwargs.get("follow_redirects") is True
