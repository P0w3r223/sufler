"""uzupelnienie-01.md §E: „brak połączeń do hostów spoza listy dozwolonych (test z zaślepką DNS)".

To kryterium odbioru nie miało testu do 2026-09-07. Kontrola hostów istniała, ale w dwóch
miejscach czytających **adres** (`apiprofile`, `client._checked_host`), a adres nie mówi, dokąd
poszło gniazdo: `httpx.Client` z domyślnym `trust_env=True` i bez podanego transportu bierze
`HTTPS_PROXY` ze środowiska, więc żądanie do `dane.biznes.gov.pl` — razem z nagłówkiem
`Authorization`, a token niesie PESEL — wychodziło na host wskazany przez zmienną środowiskową.
Sprawdzenie napisu przechodziło, połączenie szło gdzie indziej.

Zaślepką jest `socket.getaddrinfo`: notuje nazwy, o które ktoś pyta, i odmawia ich rozwiązania.
Dzięki temu testy mówią o **zamiarze połączenia**, nie o jego skutku, i nie potrzebują sieci.
Kontrola pozytywna (test niżej) pilnuje, żeby zaślepka w ogóle coś widziała — zestaw pusty
przechodziłby każdą asercję „nie pytano o obcy host" bez względu na to, czy kod działa.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from ceidg_tool import pipeline
from ceidg_tool.config import ALLOWED_HOSTS, Settings
from ceidg_tool.errors import UntrustedLinkError
from ceidg_tool.httpclient import build_http_client, build_model_http_client
from tests.conftest import FakeClock

ALLOWED_URL = "https://dane.biznes.gov.pl/api/ceidg/v3/firmy?limit=1"
FOREIGN_URL = "https://zly.example.test/api/ceidg/v3/firmy"
PROXY = "http://proxy.zly.example.test:8080"


@pytest.fixture
def resolved(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Nazwy, o których rozwiązanie poproszono; rozwiązanie zawsze się nie udaje.

    Podmienia atrapę z `conftest.no_name_resolution` na notującą. Ten sam `monkeypatch`
    obsługuje obie, więc po teście wraca prawdziwe `getaddrinfo`.
    """
    names: list[str] = []

    def record(host: object, *args: object, **kwargs: object) -> object:
        names.append(str(host))
        raise socket.gaierror("zaślepka DNS: rozwiązywanie nazw wyłączone w testach")

    monkeypatch.setattr(socket, "getaddrinfo", record)
    yield names


def test_the_suite_refuses_name_resolution_by_default() -> None:
    """Siatka bezpieczeństwa z `conftest` naprawdę zatrzymuje test wychodzący do sieci.

    Bez tego testu autouse fixture mógłby przestać działać (literówka w nazwie modułu,
    zmiana kolejności importów) i cała suita zaczęłaby po cichu tolerować prawdziwe żądania.
    """
    with pytest.raises(AssertionError, match="bez sieci"):
        socket.getaddrinfo("example.test", 443)


def test_an_allowed_host_is_the_only_name_resolved(resolved: list[str]) -> None:
    """Kontrola pozytywna: żądanie na dozwolony host pyta o dokładnie ten jeden host.

    Gdyby zaślepka nie widziała niczego, wszystkie pozostałe testy w tym pliku przechodziłyby
    z powodu pustej listy, a nie z powodu poprawnego kodu.
    """
    with build_http_client() as client:
        with pytest.raises(httpx.TransportError):
            client.get(ALLOWED_URL)

    assert resolved == ["dane.biznes.gov.pl"]


def test_a_proxy_in_the_environment_cannot_divert_the_request(
    resolved: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regresja defektu z 2026-09-07: `HTTPS_PROXY` przekierowywał żądanie razem z tokenem.

    Ustawione są wszystkie trzy zmienne, bo httpx czyta je niezależnie — sprawdzenie samego
    `HTTPS_PROXY` przechodziłoby przy kodzie honorującym `ALL_PROXY`.
    """
    for variable in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(variable, PROXY)

    with build_http_client() as client:
        with pytest.raises(httpx.TransportError):
            client.get(ALLOWED_URL)

    assert resolved == ["dane.biznes.gov.pl"]
    assert "proxy.zly.example.test" not in resolved


def test_a_foreign_host_is_refused_before_any_name_is_resolved(resolved: list[str]) -> None:
    """Bramka siedzi w transporcie, więc odmowa zapada przed pytaniem DNS, nie po nim."""
    with build_http_client() as client:
        with pytest.raises(UntrustedLinkError, match="dane.biznes.gov.pl"):
            client.get(FOREIGN_URL)

    assert resolved == []


def test_plain_http_to_an_allowed_host_is_refused(resolved: list[str]) -> None:
    """§B chce TLS z weryfikacją certyfikatu; `http://` na dozwolonym hoście to nadal brak TLS."""
    with build_http_client() as client:
        with pytest.raises(UntrustedLinkError):
            client.get("http://dane.biznes.gov.pl/api/ceidg/v3/firmy")

    assert resolved == []


def test_a_mock_transport_is_wrapped_by_the_same_gate() -> None:
    """Atrapa z testów przechodzi przez tę samą bramkę, więc testy nie jeżdżą inną ścieżką.

    To jest odpowiedź na przyczynę, dla której defekt przetrwał: `tests/support.py` budował
    własnego klienta, a httpx pomija proxy ze środowiska, gdy transport jest podany — więc
    dokładnie ten szew, który miał testować produkcję, ukrywał jej zachowanie.
    """
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))

    with build_http_client(transport=transport) as client:
        assert client.get(ALLOWED_URL).status_code == 200
        with pytest.raises(UntrustedLinkError):
            client.get(FOREIGN_URL)


def test_the_pipeline_builds_its_client_through_the_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reguła 11 mówi, że fabryka jest jedna; ten test mówi, że produkcja jej używa.

    Skan AST widzi brak drugiego konstruktora, ale nie widzi, czy ten jeden jest wywoływany —
    `build_deps` mogłoby wziąć klienta skądinąd i reguła nadal byłaby zielona. Pilnujemy też,
    że produkcja **nie** podstawia transportu: to podstawienie jest szwem testowym, a gdyby
    trafiło do produkcji, wyłączyłoby prawdziwą weryfikację certyfikatu.
    """
    calls: list[tuple[httpx.BaseTransport | None, frozenset[str]]] = []

    def spy(
        *,
        transport: httpx.BaseTransport | None = None,
        allowed: frozenset[str] = ALLOWED_HOSTS,
    ) -> httpx.Client:
        calls.append((transport, allowed))
        return build_http_client(transport=transport, allowed=allowed)

    monkeypatch.setattr(pipeline, "build_http_client", spy)
    deps = pipeline.build_deps(
        Settings(token="tok", environment="test", data_dir=tmp_path / "dane"),
        clock=FakeClock(),
    )
    try:
        assert calls == [(None, frozenset({"test-dane.biznes.gov.pl"}))]
        assert deps.client is not None
    finally:
        deps.store.close()


def test_the_test_environment_cannot_reach_production(resolved: list[str]) -> None:
    """Zawężenie bramki do hosta środowiska: `links.next` z produkcji nie wyjdzie z testu.

    Produkcja niesie prawdziwe dane osobowe, a zgoda na nią zapada raz, przy starcie
    (`--produkcja`). Bramka na oba hosty przepuszczałaby odpowiedź, która w trakcie pracy na
    teście wskaże produkcyjny adres — kontrola w `client._checked_host` też, bo ona zna listę,
    a nie wybrane środowisko.
    """
    only_test = frozenset({"test-dane.biznes.gov.pl"})

    with build_http_client(allowed=only_test) as client:
        with pytest.raises(UntrustedLinkError, match="test-dane.biznes.gov.pl"):
            client.get(ALLOWED_URL)

    assert resolved == []


def test_ssl_cert_file_in_the_environment_does_not_replace_the_ca_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§B chce weryfikacji wobec wbudowanego zestawu CA, nie wobec tego, co wskaże środowisko.

    To jedyny test, który pilnuje `trust_env=False` przekazanego `httpx.HTTPTransport` — proxy
    ze środowiska odpada już dlatego, że transport jest podawany zawsze, więc `trust_env` na
    samym kliencie jest tu drugim zamkiem, a nie mechanizmem. Bez tej asercji usunięcie
    `trust_env=False` z transportu przechodziło całą suitę: przegląd kodu 2026-09-07 pokazał to
    uruchomieniem. Wskazujemy plik, którego nie ma — przy `trust_env=True` httpx próbuje go
    wczytać jako zestaw CA i wywala się już przy konstrukcji.
    """
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "nie-ma-takiego-pliku.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "nie-ma-takiego-katalogu"))

    with build_http_client():
        pass


# --- drugie wyjście: klient modelu (ADR-0011, decyzja 1) ---------------------------------

# Te testy **nie dziedziczą** pewności po testach klienta CEIDG. `anthropic` 1.x stoi na
# `httpx2`, osobnej dystrybucji, a przeniesienie mechanizmu z `httpx` 0.28 było w ADR nazwane
# ryzykiem numer jeden. Każdy z nich buduje **prawdziwy** transport, bo to właśnie ta linia
# nie miała pokrycia — dokładnie tak jak produkcyjna konstrukcja klienta CEIDG do fazy 3f.

MODEL_URL = "https://api.anthropic.com/v1/messages"


def test_the_model_client_resolves_only_its_own_host(resolved: list[str]) -> None:
    """Kontrola pozytywna, a przy okazji odpowiedź na znalezisko F3.

    Pytanie brzmiało: czy `httpx2` rozwiązuje nazwy przez `socket.getaddrinfo`, czyli czy
    zaślepka DNS z `conftest.py` w ogóle widzi ruch do modelu. Widzi — i to jest pomiar,
    a nie założenie przeniesione z pierwszego stosu.
    """
    import httpx2

    with build_model_http_client() as client:
        with pytest.raises(httpx2.TransportError):
            client.get(MODEL_URL)

    assert resolved == ["api.anthropic.com"]


def test_a_proxy_cannot_divert_the_model_client(
    resolved: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ten sam defekt, co zamknięty dla CEIDG w fazie 3f — sprawdzony na drugim stosie."""
    import httpx2

    for variable in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(variable, PROXY)

    with build_model_http_client() as client:
        with pytest.raises(httpx2.TransportError):
            client.get(MODEL_URL)

    assert resolved == ["api.anthropic.com"]


def test_ssl_cert_file_does_not_replace_the_ca_bundle_for_the_model_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Jedyny mechanizm połowy „TLS z weryfikacją certyfikatu" na drugim stosie.

    Test buduje **prawdziwy** `httpx2.HTTPTransport` — atrapa nigdy nie dotknęłaby tej linii,
    a to ona odpowiada za całą tę własność. Przy `trust_env=True` konstrukcja wywala się na
    nieistniejącym pliku, więc przypadek rozróżnia.
    """
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "nie-ma-takiego-pliku.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "nie-ma-takiego-katalogu"))

    with build_model_http_client():
        pass


def test_a_foreign_host_is_refused_by_the_model_client_before_dns(resolved: list[str]) -> None:
    with build_model_http_client() as client:
        with pytest.raises(UntrustedLinkError, match="api.anthropic.com"):
            client.get("https://zly.example.test/v1/messages")

    assert resolved == []


def test_neither_client_can_reach_the_other_side(resolved: list[str]) -> None:
    """Obie listy hostów, oba kierunki. Dopisanie hosta do jednej z nich zapala ten test.

    Wcześniej sprawdzany był wyłącznie kierunek model → CEIDG; brakującą połową był klient
    CEIDG sięgający do modelu, czyli ta, przez którą wyszłyby **pobrane rekordy**.
    """
    with build_http_client() as ceidg:
        with pytest.raises(UntrustedLinkError):
            ceidg.get(MODEL_URL)

    with build_model_http_client() as model:
        with pytest.raises(UntrustedLinkError):
            model.get(ALLOWED_URL)

    assert resolved == []


def test_the_caller_builds_its_client_through_the_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bez tego gałąź `http_client or build_model_http_client()` nie wykonuje się nigdy.

    Każdy test `caller.py` podstawia transport, więc produkcyjna konstrukcja — ta z prawdziwym
    `httpx2.HTTPTransport` — byłaby martwym kodem. To jest ta sama pułapka, którą faza 3f
    zapłaciła po stronie CEIDG.
    """
    import httpx2

    from ceidg_tool.assistant import caller as modul

    uzyte: list[object] = []

    def spy(*, transport: object = None, allowed: frozenset[str] | None = None) -> httpx2.Client:
        uzyte.append(transport)
        return build_model_http_client(transport=transport)  # type: ignore[arg-type]

    monkeypatch.setattr(modul, "build_model_http_client", spy)
    modul.AnthropicCaller(api_key="sk-ant-test-" + "a" * 20, slownik={"6201Z": "x"})

    assert uzyte == [None], "produkcja ma budować transport sama, nie brać go z testu"
