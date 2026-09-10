"""Tryb pokazu a §B: żadne poświadczenie go nie dotyczy i żadne gniazdo się nie otwiera.

Dwa zdania z ADR-0014, obie połowy krytyczne, obie łatwe do zepsucia w jednej linii.

**Poświadczenie.** `DEFAULT_ENV_FILE` jest ścieżką **względną wobec katalogu roboczego**, więc
pokaz uruchomiony w katalogu repozytorium — czyli tak, jak uruchomi go nowy właściciel zaraz
po `git clone` — wczytałby prawdziwy `CEIDG_TOKEN` z `.env`. Ten token niesie w payloadzie
PESEL. `_settings_demo` odcina cały łańcuch: `env_file=None`, `use_keyring=False` i jawne
`token=`, które ma pierwszeństwo przed zmienną środowiskową. Żadnej nowej furtki nie ma i nie
potrzeba — `load_settings` przyjmuje te trzy argumenty od zawsze, tylko dotąd korzystały
z nich wyłącznie testy.

**Gniazdo.** Demo nie omija bramki wyjścia, tylko przez nią przechodzi: `zbuduj_demo` oddaje
`httpx.MockTransport` do tego samego `build_http_client`, więc `AllowedHostsTransport` nadal
opakowuje transport i nadal zawęża go do hosta wybranego środowiska. Podstawienie zachodzi
w **korzeniu kompozycji** (`cli`), bo gałąź `--demo` wewnątrz `build_deps` słusznie zapaliłaby
na czerwono `test_egress_allowlist.py::test_the_pipeline_builds_its_client_through_the_factory`
— test, który pilnuje, że produkcja woła `build_http_client(transport=None)`. Testy niżej
sprawdzają, że tak właśnie zostało: reguła granic 11 nietknięta, `HOST_ENVIRONMENT` bez
nowego hosta, a zaślepka DNS nie widzi ani jednego pytania.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import ceidg_tool.config as config
from ceidg_tool import cli, httpclient, pipeline
from ceidg_tool.cli import app
from ceidg_tool.config import (
    ALLOWED_HOSTS,
    DEFAULT_CACHE_TTL_DAYS,
    HOST_ENVIRONMENT,
    Settings,
)
from ceidg_tool.demo import TOKEN_DEMO
from ceidg_tool.errors import ConfigError, UntrustedLinkError
from ceidg_tool.httpclient import AllowedHostsTransport, build_http_client
from tests.conftest import FakeClock

# Token w kształcie tego, co naprawdę leży w `.env` właściciela: JWT, więc `inspect_token`
# zajrzy do payloadu i `token_source` powie „.env". Wartość jest wymyślona, ale kształt musi
# się zgadzać — token opaque przechodziłby inną gałęzią niż ten, o który tu chodzi.
TOKEN_WLASCICIELA = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ3bGFzY2ljaWVsIiwiaWF0IjoxNzAwMDAwMDAwfQ"
    ".podpis-ktorego-nikt-nie-sprawdza-w-tym-tescie"
)
TOKEN_Z_KEYRINGA = "token-z-magazynu-hasel-ktorego-demo-nie-czyta"
TOKEN_ZE_ZMIENNEJ = "token-ze-zmiennej-srodowiskowej-ktorego-demo-nie-czyta"
FOREIGN_URL = "https://zly.example.test/api/ceidg/v3/firmy"
PROD_URL = "https://dane.biznes.gov.pl/api/ceidg/v3/firmy?limit=1"


@pytest.fixture
def wszystkie_poswiadczenia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Wszystkie trzy źródła tokenu naraz — tak wygląda maszyna właściciela.

    Sprawdzanie po jednym byłoby słabsze: `_settings_demo` musi odciąć **cały** łańcuch,
    a łańcuch ma priorytety, więc test z jednym źródłem przechodziłby także wtedy, gdy
    odcięte zostało akurat to o najniższym priorytecie.

    `CEIDG_DATA_DIR` idzie **i** do `os.environ`, **i** do słownika dla `CliRunner`: część
    testów woła `cli._settings_demo` wprost, a bez zmiennej w środowisku procesu ustawienia
    wskazałyby `%LOCALAPPDATA%\\ceidg-tool` — czyli katalog z prawdziwą bazą operatora.
    Suita nie ma prawa go dotknąć nawet po to, żeby założyć podkatalog.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        f"CEIDG_TOKEN={TOKEN_WLASCICIELA}\nCEIDG_ENV=prod\n", encoding="utf-8"
    )
    monkeypatch.setattr(config, "read_token_from_keyring", lambda *_: TOKEN_Z_KEYRINGA)
    monkeypatch.setenv("CEIDG_TOKEN", TOKEN_ZE_ZMIENNEJ)
    monkeypatch.setenv("CEIDG_DATA_DIR", str(tmp_path / "dane"))
    monkeypatch.setenv("CEIDG_DEMO_TEMPO", "200")
    # Siatka bezpieczeństwa pod zmienną: gdyby `_settings_demo` przestało ją czytać, katalog
    # domyślny i tak nie wskaże katalogu operatora, a test o zmiennej zapali się osobno.
    monkeypatch.setattr(cli, "default_data_dir", lambda: tmp_path / "domyslny")
    return {"CEIDG_DATA_DIR": str(tmp_path / "dane"), "CEIDG_DEMO_TEMPO": "200"}


@pytest.fixture
def pytania_dns(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Notująca zaślepka DNS — ta sama technika co w `test_egress_allowlist.py`.

    Podmienia autouse'ową atrapę z `conftest`, więc po teście wraca prawdziwe `getaddrinfo`.
    Mówi o **zamiarze** połączenia, nie o jego skutku, i nie wymaga sieci.
    """
    nazwy: list[str] = []

    def notuj(host: object, *args: object, **kwargs: object) -> object:
        nazwy.append(str(host))
        raise socket.gaierror("zaślepka DNS: rozwiązywanie nazw wyłączone w testach")

    monkeypatch.setattr(socket, "getaddrinfo", notuj)
    yield nazwy


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setattr(cli.console, "width", 200)
    return CliRunner()


# ------------------------------------------------------------------ poświadczenia


def test_pokaz_nie_siega_po_zaden_z_trzech_zrodel_tokenu(
    wszystkie_poswiadczenia: dict[str, str],
) -> None:
    """Trzy źródła ustawione, żadne nie użyte — token pochodzi z argumentu i jest zastępczy.

    `token_source` jest tu asercją nośną, a nie ozdobą: sama równość wartości przeszłaby
    również wtedy, gdyby `load_settings` wzięło token skądinąd i przypadkiem trafiło.
    """
    settings = cli._settings_demo(None, False)

    assert settings.token == TOKEN_DEMO
    assert settings.token_source == "argument"
    assert settings.token not in (TOKEN_WLASCICIELA, TOKEN_Z_KEYRINGA, TOKEN_ZE_ZMIENNEJ)


def test_pokaz_nie_otwiera_pliku_env_wcale(
    wszystkie_poswiadczenia: dict[str, str],
) -> None:
    """`env_file=None` musi mieć **własnego** obserwatora, bo token sam go nie ma.

    `token=` ma pierwszeństwo przed plikiem, więc test patrzący wyłącznie na wartość tokenu
    przechodzi także wtedy, gdy `env_file` wróci do wartości domyślnej — sprawdzone mutacją.
    A wtedy pokaz **czyta** `.env` właściciela: nie po token, ale po wszystko inne, i po
    drodze dotyka pliku, którego dotykać nie ma. Obserwatorem jest tu wartość, która
    z `.env` pochodzić może, a z argumentów nie pochodzi.
    """
    (Path(".env")).write_text(
        f"CEIDG_TOKEN={TOKEN_WLASCICIELA}\nCEIDG_ENV=prod\nCEIDG_CACHE_TTL_DAYS=999\n",
        encoding="utf-8",
    )

    settings = cli._settings_demo(None, False)

    assert settings.cache_ttl_days == DEFAULT_CACHE_TTL_DAYS, "demo wczytało .env z katalogu"


def test_pokaz_nie_pyta_magazynu_hasel_o_nic(
    wszystkie_poswiadczenia: dict[str, str],
) -> None:
    """`use_keyring=False` też potrzebuje własnego obserwatora, i z tego samego powodu.

    Magazyn haseł trzyma **dwa** sekrety (token CEIDG i klucz asystenta), a `use_keyring`
    steruje odczytem obu. Token przykrywa jawne `token=`, więc widać wyłącznie po kluczu
    asystenta: gdyby magazyn był pytany, pokaz wystartowałby z sekretem, którego nikt mu
    na pokaz nie dał — i z włączonym asystentem, czyli z drugim wyjściem do sieci.
    """
    settings = cli._settings_demo(None, False)

    assert settings.anthropic_key is None
    assert settings.anthropic_key_source is None


def test_klucz_asystenta_ze_zmiennej_srodowiskowej_dociera_swiadomie(
    wszystkie_poswiadczenia: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wyjątek zapisany w `_settings_demo` wprost: `ANTHROPIC_API_KEY` **ma** przechodzić.

    Bez tego testu poprzedni czytałby się jak „w demie nie ma asystenta", a to nieprawda:
    pokaz asystenta na żywo jest jednym z powodów, dla których `environ` zostaje prawdziwe.
    Granica biegnie po tokenie CEIDG (nigdy) i po magazynie haseł (nigdy), nie po zmiennych.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "a" * 24)

    settings = cli._settings_demo(None, False)

    assert settings.anthropic_key_source == "env"
    assert settings.token == TOKEN_DEMO


def test_pokaz_zostaje_w_srodowisku_test_mimo_ceidg_env_prod_w_pliku(
    wszystkie_poswiadczenia: dict[str, str],
) -> None:
    """`.env` z `CEIDG_ENV=prod` nie może przestawić pokazu na produkcję bocznymi drzwiami.

    Odmowa z `_settings_demo` czyta **argumenty**, a nie plik; gdyby środowisko było brane
    z `pick(ENV_ENVIRONMENT)`, znacznik piąty dałoby się obejść bez żadnej flagi.
    """
    settings = cli._settings_demo(None, False)

    assert settings.environment == "test"


def test_pokaz_pracuje_na_kopii_katalogu_a_nie_na_katalogu_produkcyjnym(
    wszystkie_poswiadczenia: dict[str, str], tmp_path: Path
) -> None:
    """`CEIDG_DATA_DIR` nadal działa, ale baza wisi o katalog niżej — znacznik czwarty."""
    settings = cli._settings_demo(None, False)

    assert settings.data_dir == tmp_path / "dane" / "demo"
    assert settings.store_path.parent.name == "demo"


@pytest.mark.parametrize(
    ("srodowisko", "produkcja"), [("prod", False), (None, True), ("PROD", True)]
)
def test_settings_demo_odmawia_produkcji_zanim_cokolwiek_powstanie(
    wszystkie_poswiadczenia: dict[str, str],
    tmp_path: Path,
    srodowisko: str | None,
    produkcja: bool,
) -> None:
    """Odmowa jest pierwszą instrukcją, więc katalog danych ani log nie zdążą powstać."""
    with pytest.raises(ConfigError, match="demo"):
        cli._settings_demo(srodowisko, produkcja)

    assert not (tmp_path / "dane").exists()


def test_zaden_prawdziwy_token_nie_trafia_do_artefaktow_pokazu(
    wszystkie_poswiadczenia: dict[str, str], runner: CliRunner, tmp_path: Path
) -> None:
    """§D chce zera trafień po `grep` całej sesji — tu także w plikach, które po niej zostają.

    Ekran, plik logu i skoroszyt naraz, bo każdy z nich ma inną drogę wyjścia i każdą z nich
    ten projekt już raz zgubił osobno.

    Asercja o źródle tokenu nie jest tu ozdobą i została dopisana po mutacji. Usunięcie
    `token=TOKEN_DEMO` z `_settings_demo` **nie** zapala samego `grep`: token ze zmiennej
    środowiskowej wchodzi wtedy do `_KNOWN_SECRETS` i maskowanie zakrywa go w tych samych
    plikach. Test mówiłby więc „nie wyciekł", opisując świat, w którym prawdziwe
    poświadczenie jest wczytane, trzymane w pamięci procesu i tylko zamaskowane w druku.
    Gwarancją ma być to, że nie zostało wczytane — a to widać po źródle.
    """
    result = runner.invoke(
        app, ["pobierz", "-w", "podlaskie", "--demo", "--tak"], env=wszystkie_poswiadczenia
    )
    assert result.exit_code == 0, result.output

    pliki = [p for p in (tmp_path / "dane").rglob("*") if p.is_file()]
    tresc = b"".join(p.read_bytes() for p in pliki)

    assert pliki, "przebieg nie zostawił żadnego pliku — test nie miałby czego sprawdzać"
    assert "źródło: argument" in result.output
    for sekret in (TOKEN_WLASCICIELA, TOKEN_Z_KEYRINGA, TOKEN_ZE_ZMIENNEJ):
        assert sekret not in result.output
        assert sekret.encode() not in tresc


# ------------------------------------------------------------------ gniazdo i bramka wyjścia


def test_pokaz_nie_prosi_o_rozwiazanie_zadnej_nazwy(
    wszystkie_poswiadczenia: dict[str, str],
    runner: CliRunner,
    pytania_dns: list[str],
) -> None:
    """Pełne pobranie z eksportem i ani jednego pytania do DNS — „zero żądań do CEIDG" dosłownie.

    Zaślepka notuje **zamiar**, więc test nie zależy od tego, czy maszyna ma sieć. Kontrolę
    pozytywną (że zaślepka w ogóle coś widzi) niesie test niżej.
    """
    result = runner.invoke(
        app, ["pobierz", "-w", "podlaskie", "--demo", "--tak"], env=wszystkie_poswiadczenia
    )

    assert result.exit_code == 0, result.output
    assert pytania_dns == []


def test_zaslepka_dns_widzi_ruch_wychodzacy_z_tego_samego_klienta(
    pytania_dns: list[str],
) -> None:
    """Kontrola pozytywna do testu wyżej: bez niej pusta lista niczego nie dowodzi.

    Ten sam `build_http_client`, tylko bez podstawionego transportu — czyli dokładnie ta
    ścieżka, którą pokaz **nie** idzie.
    """
    with build_http_client() as klient:
        with pytest.raises(httpx.TransportError):
            klient.get(PROD_URL)

    assert pytania_dns == ["dane.biznes.gov.pl"]


def test_klient_pokazu_nadal_odrzuca_obcy_host(
    wszystkie_poswiadczenia: dict[str, str], pytania_dns: list[str]
) -> None:
    """Atrapa transportu nie wyłącza bramki — jest przez nią opakowana, jak każdy transport.

    Gdyby demo budowało `httpx.Client` samo (a to jest najkrótsza droga do działającego
    pokazu), reguła granic 11 przestałaby odpowiadać na pytanie „dokąd może wyjść połączenie"
    dla trybu, który operator ma pod ręką w każdym poleceniu.
    """
    _, demo = cli._demo_deps(cli._settings_demo(None, False))
    try:
        with pytest.raises(UntrustedLinkError, match="test-dane.biznes.gov.pl"):
            demo.klient.get(FOREIGN_URL)
        # Zawężenie do hosta **wybranego środowiska**, nie do całej listy dozwolonych:
        # produkcja jest dla pokazu równie obca jak `zly.example.test`.
        with pytest.raises(UntrustedLinkError):
            demo.klient.get(PROD_URL)
    finally:
        demo.klient.close()

    assert pytania_dns == []


def test_klient_pokazu_powstaje_w_httpclient_i_jest_opakowany_bramka(
    wszystkie_poswiadczenia: dict[str, str],
) -> None:
    """Reguła 11 mówi „jedna fabryka"; ten test mówi, że demo jej używa i nie rozpakowuje.

    Skan AST z `test_boundaries.py` widzi brak drugiego konstruktora, ale nie widzi, czy
    ten jeden został wywołany i czy jego wynik nie został podmieniony po drodze.
    """
    _, demo = cli._demo_deps(cli._settings_demo(None, False))
    try:
        transport = demo.klient._transport
        assert isinstance(transport, AllowedHostsTransport)
        assert isinstance(transport._inner, httpx.MockTransport)
        assert transport._allowed == frozenset({"test-dane.biznes.gov.pl"})
    finally:
        demo.klient.close()


def test_podstawienie_zachodzi_w_cli_a_nie_w_build_deps(
    wszystkie_poswiadczenia: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sedno decyzji z ADR-0014: `build_deps` nie zna trybu demo i nie ma go poznać.

    Gałąź `--demo` w środku `build_deps` zapaliłaby na czerwono stojący strażnik
    (`test_egress_allowlist.py::test_the_pipeline_builds_its_client_through_the_factory`),
    który pilnuje, że produkcja buduje klienta przez `build_http_client(transport=None)`.
    Ten test pilnuje drugiej strony tej samej równowagi: skoro demo dostaje gotowego klienta
    przez `http=`, `pipeline` nie ma powodu wołać fabryki ani razu.
    """
    z_pipeline: list[object] = []
    z_demo: list[object] = []

    def szpieg_pipeline(**kwargs: object) -> httpx.Client:
        z_pipeline.append(kwargs.get("transport"))
        return build_http_client(**kwargs)  # type: ignore[arg-type]

    def szpieg_demo(**kwargs: object) -> httpx.Client:
        z_demo.append(kwargs.get("transport"))
        return build_http_client(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline, "build_http_client", szpieg_pipeline)
    monkeypatch.setattr("ceidg_tool.demo.build_http_client", szpieg_demo)

    deps, demo = cli._demo_deps(cli._settings_demo(None, False))
    try:
        assert z_pipeline == [], "pipeline zbudował własnego klienta mimo podanego http="
        assert len(z_demo) == 1
        assert isinstance(z_demo[0], httpx.MockTransport)
        assert deps.demo is True
    finally:
        demo.klient.close()
        deps.store.close()


def test_build_deps_bez_demo_zostawia_znacznik_wylaczony(
    tmp_path: Path,
) -> None:
    """`Deps.demo` domyślnie fałszywe, a `build_deps` go nie rusza — inaczej każda ścieżka,
    która pominie korzeń kompozycji, oznaczałaby pobranie jako pokaz albo odwrotnie."""
    deps = pipeline.build_deps(
        Settings(token="tok", environment="test", data_dir=tmp_path / "dane"),
        clock=FakeClock(),
        online=False,
    )
    try:
        assert deps.demo is False
    finally:
        deps.store.close()


def test_tryb_demo_nie_dokladal_hosta_do_polityki_wyjscia() -> None:
    """ADR-0014 kupuje pokaz **bez** poszerzania mapy hostów; to jest cena, której nie zapłacono.

    Trzecie środowisko albo lokalny serwer atrapy oznaczałyby trwałe rozluźnienie reguły 11
    w zamian za etykietę. Dopisanie tu hosta zapala ten test i wraca do tamtej rozmowy.
    """
    assert ALLOWED_HOSTS == frozenset({"dane.biznes.gov.pl", "test-dane.biznes.gov.pl"})
    assert set(HOST_ENVIRONMENT) == ALLOWED_HOSTS
    assert "demo" not in set(HOST_ENVIRONMENT.values())
    assert httpclient.build_http_client.__defaults__ is None  # `allowed` idzie przez keyword
