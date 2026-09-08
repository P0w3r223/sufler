"""Pięć znaczników trybu pokazu (ADR-0014) — bo bez nich ta decyzja jest najgorszą z czterech.

Demo jest własnością **produktu**, a nie osobnym programem: `ceidg-tool pobierz --demo` idzie
tą samą ścieżką co praca. Cena jest jedna i ADR nazywa ją wprost — istnieje tryb, w który
operator może wejść nie zauważywszy, a skoroszyt z pokazu jest wtedy nie do odróżnienia od
produkcyjnego. Odpowiedzią jest pięć znaczników, obowiązkowych łącznie:

1. pierwszy ekran,
2. wiersz w arkuszu `Metadane`,
3. prefiks `DEMO_` w nazwie pliku,
4. osobny katalog danych,
5. odmowa połączenia `--demo` z produkcją.

Znacznik numer dwa dostaje tu najostrzejszy test i sprawdzany jest na **prawdziwym pliku**,
bo jako jedyny podróżuje razem ze skoroszytem: ekran widzi ten, kto siedział przy pokazie,
a plik trafia dalej i musi sam o sobie mówić. Znacznik numer cztery jako jedyny chroni coś
poza czytelnością — baza demo nie może dotknąć produkcyjnej, bo to inny plik.

Wszystkie testy uruchamiające CLI ustawiają `CEIDG_DATA_DIR` na `tmp_path`: bez tego pokaz
pisałby do prawdziwego katalogu roboczego operatora.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from openpyxl import load_workbook
from typer.testing import CliRunner

import ceidg_tool.config as config
from ceidg_tool import cli
from ceidg_tool.cli import app
from ceidg_tool.config import DEMO_OSTRZEZENIE, Settings
from ceidg_tool.demo import TOKEN_DEMO
from ceidg_tool.pipeline import Deps, build_deps, output_name
from ceidg_tool.ui import texts
from tests.conftest import FakeClock
from tests.support import FakeApi, criteria, load_fixture

EXIT_CONFIG = 3
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
VERSION = "0.1.0"
# Polecenia przyjmujące `--demo`. `raporty`, `token` i `wyczysc` świadomie nie są tu wymienione:
# pierwsze pobiera archiwum, którego atrapa nie udaje, dwa pozostałe dotyczą poświadczeń
# i katalogu, a nie pobierania.
POLECENIA_Z_DEMO = ("kreator", "pobierz", "aktualizuj", "wznow", "eksportuj", "runy")


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    """CLI w pustym katalogu roboczym, bez magazynu haseł, z przypiętą szerokością konsoli."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "read_token_from_keyring", lambda *_: None)
    # Szerokość i kolor przypięte z tego samego powodu co w `tests/test_cli.py`: asercje
    # dotyczą treści zdania, a nie tego, ile kolumn ma terminal, na którym akurat biegną.
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setattr(cli.console, "width", 200)
    # Katalog domyślny przekierowany pod `tmp_path`. Nie jest to ozdoba: przy regresji, w której
    # `_settings_demo` przestaje czytać `CEIDG_DATA_DIR`, testy niżej pisałyby do
    # `%LOCALAPPDATA%\\ceidg-tool` — czyli obok prawdziwej bazy operatora. Sprawdzone mutacją:
    # bez tej linii przebieg z mutacją „demo ignoruje CEIDG_DATA_DIR" zakładał tam katalog.
    monkeypatch.setattr(cli, "default_data_dir", lambda: tmp_path / "domyslny")
    return CliRunner()


@pytest.fixture
def env_demo(tmp_path: Path) -> dict[str, str]:
    """Środowisko pokazu. Bez `CEIDG_TOKEN` — demo ma działać bez żadnego poświadczenia.

    `CEIDG_DEMO_TEMPO` ścisnięte, bo w suicie nie ma widowni, której trzeba pokazać pasek —
    domyślne osiem razy dawałoby na te przebiegi kilkanaście sekund. **Nie do granic
    możliwości**: przy wartościach rzędu 10⁵ `RateLimiter` przestaje dochodzić do wolnego
    slotu (zmierzone 2026-09-08), dlatego `_tempo()` odmawia powyżej 5000. 200 to 18 ms
    na żądanie, czyli daleko od obu krawędzi.
    """
    return {"CEIDG_DATA_DIR": str(tmp_path / "dane"), "CEIDG_DEMO_TEMPO": "200"}


def ustawienia(*, srodowisko: str = "test") -> Settings:
    return Settings(token=TOKEN_DEMO, environment=srodowisko, data_dir=Path("/dane"))  # type: ignore[arg-type]


def pola(block: texts.Block) -> dict[str, str]:
    return {row[0]: row[1] for row in block.rows}


def jedna_strona() -> FakeApi:
    """Atrapa API dla przebiegu **bez** demo: jedna strona, `next == self`, koniec.

    Potrzebna do kontroli negatywnej znacznika 2 — bez pobrania z prawdziwej ścieżki nie
    ma skoroszytu, w którym można sprawdzić, że wiersza `UWAGA` tam nie ma.
    """
    api = FakeApi()
    strona = dict(load_fixture("firmy_page0_limit5.json")["body"])
    strona["links"] = {**strona["links"], "next": strona["links"]["self"]}
    # `count` z fixture to 6 316 121, czyli cały rejestr — w trybie `--tak` przekracza próg
    # i program słusznie odmawia decyzji za operatora. Tu liczy się tylko skoroszyt.
    strona["count"] = len(strona["firmy"])

    def fallback(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("limit") == "1":
            return httpx.Response(200, json={**strona, "firmy": strona["firmy"][:1]})
        return httpx.Response(200, json=strona)

    api.fallback = fallback
    return api


def pobierz_pokaz(runner: CliRunner, env: dict[str, str]) -> None:
    """Jedno pełne pobranie w trybie pokazu; niepowodzenie jest tu błędem przygotowania."""
    result = runner.invoke(app, ["pobierz", "-w", "podlaskie", "--demo", "--tak"], env=env)
    assert result.exit_code == 0, result.output


def skoroszyt(katalog: Path) -> Path:
    pliki = sorted(katalog.glob("*.xlsx"))
    assert len(pliki) == 1, f"oczekiwano jednego skoroszytu, są: {[p.name for p in pliki]}"
    return pliki[0]


# --------------------------------------------------- znacznik 1: pierwszy ekran


def test_pierwszy_ekran_mowi_o_pokazie_w_tytule_i_w_pierwszym_wierszu() -> None:
    """Ostrzeżenie musi być pierwsze, bo ekran czyta się od góry i zwykle tylko od góry.

    Wiersz doklejony na końcu, pod „dane i wyniki", spełniałby literę ADR-0014 i nie
    spełniał jego powodu.
    """
    block = texts.first_screen(ustawienia(), now=NOW, version=VERSION, demo=True)

    assert block.rows[0] == ("UWAGA", DEMO_OSTRZEZENIE)
    assert "POKAZ" in block.title
    assert pola(block)["środowisko"] == "POKAZ (bez rejestru)"
    assert "syntetyczny" in pola(block)["dokąd wysyłam rekordy"]
    assert "biznes.gov.pl" not in pola(block)["dokąd wysyłam rekordy"]


def test_pierwszy_ekran_bez_demo_nie_wspomina_o_pokazie() -> None:
    """Kontrola negatywna: bez tego test wyżej przechodziłby także przy ostrzeżeniu na stałe.

    A ostrzeżenie na stałe jest gorsze niż jego brak — operator uczy się je pomijać wzrokiem
    i przestaje odróżniać pokaz od pracy w drugą stronę.
    """
    block = texts.first_screen(ustawienia(), now=NOW, version=VERSION)

    assert "UWAGA" not in dict(pola(block))
    assert "POKAZ" not in block.title
    assert DEMO_OSTRZEZENIE not in block.as_text()
    assert pola(block)["środowisko"] == "TEST"


def test_pierwszy_ekran_pokazu_nie_namawia_na_produkcje() -> None:
    """Poza demem stopka podpowiada `--srodowisko prod`; w demie ta podpowiedź jest pułapką.

    Prowadzi wprost do jedynego układu, którego `_settings_demo` odmawia, a operator dostałby
    ją w tej samej ramce, w której właśnie przeczytał, że dane są wymyślone.
    """
    block = texts.first_screen(ustawienia(), now=NOW, version=VERSION, demo=True)

    assert texts.PROD_HINT not in block.notes


def test_pierwszy_ekran_pokazu_pada_takze_z_linii_polecen(
    runner: CliRunner, env_demo: dict[str, str]
) -> None:
    """`texts.first_screen` może być poprawne, a `cli` może go nie wywołać z `demo=True`.

    Ten szew był już raz źródłem defektu: `eksportuj` obiecywał zdanie „Zapisuję skoroszyt",
    którego jako jedyne polecenie nie wypisywało (audyt 2026-09-07).
    """
    result = runner.invoke(app, ["pobierz", "-w", "podlaskie", "--demo", "--tak"], env=env_demo)

    assert result.exit_code == 0, result.output
    assert "POKAZ" in result.output
    assert "TRYB POKAZU" in result.output


# --------------------------------------------------- znacznik 2: wiersz w `Metadane`


def test_skoroszyt_z_pokazu_niesie_wiersz_uwaga_jako_pierwszy(
    runner: CliRunner, env_demo: dict[str, str], tmp_path: Path
) -> None:
    """Najostrzejszy z pięciu, bo jako jedyny podróżuje razem z plikiem.

    Sprawdzane na **prawdziwym skoroszycie** czytanym przez `openpyxl`, a nie na liście krotek
    z `build_metadata`: między jednym a drugim stoi eksporter, który mógłby wiersz pominąć,
    obciąć albo zapisać jako pusty. Pozycja też jest asercją — `Metadane` mają kilkanaście
    wierszy i ostrzeżenie na dole czyta się dopiero po przewinięciu.
    """
    result = runner.invoke(app, ["pobierz", "-w", "podlaskie", "--demo", "--tak"], env=env_demo)
    assert result.exit_code == 0, result.output

    arkusz = load_workbook(skoroszyt(tmp_path / "dane" / "demo" / "wyniki"))["Metadane"]
    wiersze = [(r[0].value, r[1].value) for r in arkusz.iter_rows(min_row=2)]

    assert wiersze[0] == ("UWAGA", DEMO_OSTRZEZENIE)
    assert "WYMYŚLONE" in str(wiersze[0][1])
    # Ostrzeżenie nie zastępuje reszty metadanych — audyt pobrania ma nadal działać.
    assert {"kryteria", "srodowisko", "run_id"} <= {k for k, _ in wiersze}


def test_skoroszyt_z_pracy_nie_niesie_wiersza_uwaga(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kontrola negatywna dla znacznika 2, na tej samej drodze eksportu.

    Bez niej wiersz mógłby stać w `Metadane` **zawsze** i test wyżej przechodziłby, a arkusz
    z prawdziwego pobrania ostrzegałby, że dane są wymyślone — czyli kłamał w drugą stronę.
    Pobranie jest tu puste (`-n` na nieistniejącą nazwę), bo chodzi wyłącznie o metadane.
    """
    api = jedna_strona()

    def przez_atrape(settings: Settings, **reszta: object) -> Deps:
        return build_deps(settings, http=api.client(), clock=FakeClock(), **reszta)  # type: ignore[arg-type]

    monkeypatch.setattr(cli, "build_deps", przez_atrape)
    env = {"CEIDG_TOKEN": "tok", "CEIDG_DATA_DIR": str(tmp_path / "dane")}

    result = runner.invoke(app, ["pobierz", "-w", "podlaskie", "--zrodlo", "api", "--tak"], env=env)
    assert result.exit_code == 0, result.output

    arkusz = load_workbook(skoroszyt(tmp_path / "dane" / "wyniki"))["Metadane"]
    klucze = [r[0].value for r in arkusz.iter_rows(min_row=2)]

    assert "UWAGA" not in klucze


# --------------------------------------------------- znacznik 3: prefiks nazwy pliku


def test_nazwa_pliku_z_pokazu_zaczyna_sie_od_demo() -> None:
    """Nazwa pliku jest tym, co widać w katalogu i w załączniku — czyli po pokazie."""
    zapytanie = criteria(wojewodztwo="wielkopolskie")

    pokaz = output_name(zapytanie, "test", NOW, demo=True)
    praca = output_name(zapytanie, "test", NOW)

    assert pokaz.startswith("DEMO_")
    assert not praca.startswith("DEMO_")
    assert pokaz.endswith(praca), "prefiks ma być doklejony, a nie zastąpić nazwę"


def test_skoroszyt_z_pokazu_zapisuje_sie_pod_nazwa_z_prefiksem(
    runner: CliRunner, env_demo: dict[str, str], tmp_path: Path
) -> None:
    """`output_name` może być poprawne, a droga do niego — nie. Ten sam szew co przy ekranie."""
    result = runner.invoke(app, ["pobierz", "-w", "podlaskie", "--demo", "--tak"], env=env_demo)
    assert result.exit_code == 0, result.output

    plik = skoroszyt(tmp_path / "dane" / "demo" / "wyniki")

    assert plik.name.startswith("DEMO_")


def test_ponowny_eksport_zachowuje_znaczniki_pokazu(
    runner: CliRunner, env_demo: dict[str, str], tmp_path: Path
) -> None:
    """`eksportuj` istnieje po to, żeby zrobić **drugą kopię** pliku — z tymi samymi znacznikami.

    Do naprawy z 2026-09-08 nie zachowywał żadnego z dwóch, które podróżują z plikiem: nazwa
    wychodziła bez `DEMO_`, a `Metadane` bez wiersza `UWAGA`, więc druga kopia skoroszytu
    z pokazu była nie do odróżnienia od produkcyjnej. Przyczyną było `build_deps(..., online=False)`
    bez ustawienia `deps.demo`. Dokładnie to ryzyko, dla którego ADR-0014 wylicza pięć
    znaczników i pisze, że są obowiązkowe **łącznie**.
    """
    pobierz_pokaz(runner, env_demo)
    wyniki = tmp_path / "dane" / "demo" / "wyniki"
    for stary in wyniki.glob("*.xlsx"):
        stary.unlink()

    result = runner.invoke(app, ["eksportuj", "--demo"], env=env_demo)
    assert result.exit_code == 0, result.output

    plik = skoroszyt(wyniki)
    klucze = [r[0].value for r in load_workbook(plik)["Metadane"].iter_rows(min_row=2)]

    assert plik.name.startswith("DEMO_")
    assert "UWAGA" in klucze


# --------------------------------------------------- znacznik 4: osobny katalog danych


def test_pokaz_pracuje_we_wlasnej_bazie_a_produkcyjnej_nie_dotyka(
    runner: CliRunner, env_demo: dict[str, str], tmp_path: Path
) -> None:
    """Jedyny znacznik chroniący coś poza czytelnością: to inny plik, więc nie ma jak zmieszać.

    ADR-0014 odkłada mocniejszą formę (wartość `'DEMO'` w `CHECK` kolumny `firma.zrodlo`,
    czyli schemat v4) i wprost pisze, że do tego czasu **osobny katalog jest tym, co chroni
    prawdziwą bazę**. Baza produkcyjna ma po pokazie nie istnieć.
    """
    dane = tmp_path / "dane"

    result = runner.invoke(app, ["pobierz", "-w", "podlaskie", "--demo", "--tak"], env=env_demo)

    assert result.exit_code == 0, result.output
    assert (dane / "demo" / "store-test.sqlite").exists()
    assert not (dane / "store-test.sqlite").exists()
    assert not (dane / "wyniki").exists()
    assert (dane / "demo" / "logi").is_dir()


def test_pokaz_szanuje_ceidg_data_dir_zamiast_pisac_do_katalogu_domyslnego(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Zignorowanie tej zmiennej odbierałoby operatorowi jedyne „pracuj tutaj".

    Konsekwencja byłaby odwrotna do zamierzonej: pokaz pisałby do
    `%LOCALAPPDATA%\\ceidg-tool`, czyli do katalogu z prawdziwymi danymi — obok bazy,
    od której ma trzymać się z daleka.
    """
    gdzie_indziej = tmp_path / "gdzie-indziej"
    # Wartość legalna: `_tempo()` odmawia powyżej 5000, a test ma sprawdzać katalog danych,
    # nie granicę tempa. Poprzednie 100000 przechodziło tylko dlatego, że `runy` nie buduje
    # klienta demo, więc `_tempo()` nie było w ogóle wołane.
    env = {"CEIDG_DATA_DIR": str(gdzie_indziej), "CEIDG_DEMO_TEMPO": "200"}

    result = runner.invoke(app, ["runy", "--demo"], env=env)

    assert result.exit_code == 0, result.output
    assert (gdzie_indziej / "demo" / "store-test.sqlite").exists()


def test_pokaz_i_praca_nie_widza_swoich_runow(
    runner: CliRunner, env_demo: dict[str, str], tmp_path: Path
) -> None:
    """Rozdział katalogów ma być widoczny w tym, co narzędzie **mówi**, nie tylko w ścieżce.

    `runy` bez `--demo` po pokazie nie może wymienić przebiegu z pokazu: to jest ta lista,
    z której operator wybiera run do ponownego eksportu.
    """
    pobierz_pokaz(runner, env_demo)

    z_pokazu = runner.invoke(app, ["runy", "--demo"], env=env_demo)
    z_pracy = runner.invoke(
        app,
        ["runy"],
        env={"CEIDG_TOKEN": "tok", "CEIDG_DATA_DIR": str(tmp_path / "dane")},
    )

    assert "podlaskie" in z_pokazu.output
    assert "podlaskie" not in z_pracy.output


# --------------------------------------------------- znacznik 5: odmowa produkcji


@pytest.mark.parametrize("polecenie", POLECENIA_Z_DEMO)
@pytest.mark.parametrize(
    "flagi", [["-s", "prod"], ["--produkcja"], ["-s", "prod", "--produkcja"]], ids=str
)
def test_demo_odmawia_polaczenia_z_produkcja(
    runner: CliRunner,
    env_demo: dict[str, str],
    tmp_path: Path,
    polecenie: str,
    flagi: list[str],
) -> None:
    """Odmowa dotyczy **każdej** drogi, nie tylko `pobierz`, i zapada przed powstaniem bazy.

    Sama flaga `--produkcja` bez `--srodowisko` też jest odmową: nie ma czego dotyczyć, skoro
    odpowiada rejestr syntetyczny, a razem z nią łatwo pomylić, co jest na ekranie.
    """
    result = runner.invoke(app, [polecenie, *flagi, "--demo"], env=env_demo)

    assert result.exit_code == EXIT_CONFIG, result.output
    assert "demo" in result.output.lower()
    assert not (tmp_path / "dane").exists(), "odmowa zapadła po utworzeniu katalogu danych"


def test_odmowa_produkcji_nie_zalezy_od_wielkosci_liter_ani_spacji(
    runner: CliRunner, env_demo: dict[str, str]
) -> None:
    """`-s ' PROD '` to ta sama prośba; odmowa czytająca tylko `"prod"` byłaby do obejścia.

    Kryteria są tu podane celowo. Bez `-w` polecenie kończy się kodem 3 z zupełnie innego
    powodu („nie podano kryteriów"), więc test przechodziłby także przy odmowie ślepej na
    wielkość liter — sprawdzone mutacją i przez nią poprawione.
    """
    result = runner.invoke(
        app, ["pobierz", "-w", "podlaskie", "-s", " PROD ", "--demo", "--tak"], env=env_demo
    )

    assert result.exit_code == EXIT_CONFIG, result.output
    assert "demo" in result.output.lower()


def test_bez_demo_prod_nadal_wymaga_zgody_a_nie_dostaje_odmowy_o_pokazie(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Kontrola negatywna: nowa odmowa nie może przykryć starej bramki zgody na produkcję.

    Obie kończą się tym samym kodem wyjścia, więc bez sprawdzenia **treści** komunikatu
    ten test nie odróżniałby ich od siebie.
    """
    env = {"CEIDG_TOKEN": "tok", "CEIDG_DATA_DIR": str(tmp_path / "dane")}

    result = runner.invoke(app, ["pobierz", "-w", "podlaskie", "-s", "prod", "--tak"], env=env)

    assert result.exit_code == EXIT_CONFIG
    assert "--produkcja" in result.output
    assert "pokazu" not in result.output.lower()


# --------------------------------------------------- powierzchnia poleceń


@pytest.mark.parametrize("polecenie", POLECENIA_Z_DEMO)
def test_flaga_demo_jest_w_pomocy_polecenia(runner: CliRunner, polecenie: str) -> None:
    """Flaga bez opisu w `--help` jest funkcją, o której nikt się nie dowie.

    ADR-0014 stawia na tym całą wartość: „nowy właściciel może uruchomić narzędzie w dniu,
    w którym je sklonuje". Nie może, jeśli o trybie nie ma gdzie przeczytać.
    """
    result = runner.invoke(app, [polecenie, "--help"])

    assert result.exit_code == 0, result.output
    assert "--demo" in result.output
    assert "zero żądań" in result.output
