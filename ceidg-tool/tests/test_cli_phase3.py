"""Nowa powierzchnia CLI z fazy 3: `kreator`, `sprawdz-nip`, `--partie` i wejście bez argumentów.

`tests/test_cli.py` zostaje nietknięty jako bramka regresji dla zgody na produkcję — nowe
przypadki mieszkają tutaj. Sprawdzamy to, co widać z linii poleceń: że kreator odmawia poza
terminalem, że `sprawdz-nip` liczy sumę kontrolną przed siecią, i że flagi oraz plik YAML
z tymi samymi kryteriami pokazują **tę samą tabelę kosztów** (ADR-0008, decyzja 2).
"""

from __future__ import annotations

import shlex
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import ceidg_tool.config as config
from ceidg_tool import cli
from ceidg_tool.cli import app
from ceidg_tool.client import CeidgClient
from ceidg_tool.config import Settings
from ceidg_tool.criteria import Criteria
from ceidg_tool.pipeline import Deps, build_deps
from ceidg_tool.progress import Events
from ceidg_tool.records import Report
from ceidg_tool.ui import texts
from ceidg_tool.ui.prompts import interactive_available
from tests.conftest import FakeClock
from tests.support import FakeApi, load_fixture

TOKEN = "token-testowy-nie-jwt"
EXIT_CONFIG = 3
NIP = "3563457932"


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.chdir(tmp_path)
    # `*_` bo funkcja bierze teraz nazwę pozycji w magazynie (token CEIDG albo klucz
    # asystenta). Atrapa bez tego parametru wywracała **każdy** test CLI naraz.
    monkeypatch.setattr(config, "read_token_from_keyring", lambda *_: None)
    # Stała, szeroka konsola dla `--help`. `rich` dobiera szerokość z terminala, a w wąskim
    # obcina kolumnę opcji: przy 40 kolumnach `--partie` i `--pkd-2007` **znikają** z pomocy.
    # Pierwszy w historii przebieg CI (2026-09-08, Linux) wywrócił na tym pięć testów, które
    # lokalnie były zielone — a pytają one o to, czy flaga **istnieje**, nie czy mieści się
    # w N kolumnach. Bez tego test mierzył szerokość maszyny, na której akurat biegnie.
    monkeypatch.setenv("COLUMNS", "200")
    # Kolor wyłączony, bo `rich` koloruje **nazwę opcji**, rozbijając ją sekwencjami ANSI:
    # przy włączonym kolorze `"--partie" in result.output` jest fałszem, choć flaga jest na
    # ekranie. Na GitHub Actions kolor jest domyślnie włączony, więc pierwszy przebieg CI
    # wywrócił na tym pięć testów zielonych lokalnie. `NO_COLOR` nie przebija `FORCE_COLOR`
    # w tej wersji `rich`, `TERM=dumb` przebija — i dlatego jest tu jeszcze test-strażnik.
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    # Konsola aplikacji powstaje na poziomie modułu, więc szerokość wzięła już z terminala,
    # zanim `monkeypatch` doszedł do głosu — trzeba ją ustawić wprost. Bez tego test o **treści**
    # zdania mierzy szerokość maszyny, na której akurat biegnie.
    monkeypatch.setattr(cli.console, "width", 200)
    return CliRunner()


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return {"CEIDG_TOKEN": TOKEN, "CEIDG_DATA_DIR": str(tmp_path / "dane")}


def patch_transport(monkeypatch: pytest.MonkeyPatch, api: FakeApi) -> None:
    """Atrapa transportu pod `build_deps` w CLI — cały plik działa offline."""

    def fake(settings: Settings, *, events: Events | None = None, **_: object) -> Deps:
        return build_deps(settings, events=events, http=api.client(), clock=FakeClock())

    monkeypatch.setattr("ceidg_tool.cli.build_deps", fake)


def counting_api(count: int) -> FakeApi:
    api = FakeApi()

    def fallback(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("limit") == "1":
            return httpx.Response(200, json={"count": count, "firmy": []})
        return httpx.Response(204)

    api.fallback = fallback
    return api


# ----------------------------------------------------------------------------- rejestracja poleceń


@pytest.mark.parametrize("command", ["kreator", "sprawdz-nip"], ids=["kreator", "sprawdz_nip"])
def test_the_new_commands_are_registered(runner: CliRunner, command: str) -> None:
    """Polecenie musi być widoczne w pomocy — inaczej operator go nie znajdzie."""
    result = runner.invoke(app, ["--help"])

    assert command in result.output


def test_wyjscie_cli_w_testach_nie_niesie_sekwencji_ansi(runner: CliRunner) -> None:
    """Strażnik dla wszystkich asercji o treści komunikatów w tym pliku i w `test_cli.py`.

    `rich` koloruje nazwę opcji, wstawiając sekwencje ANSI **w środek** napisu, więc
    `"--partie" in result.output` staje się fałszem, choć flaga jest na ekranie. Kolor bierze
    się z otoczenia (GitHub Actions włącza go domyślnie), a nie z naszego kodu — pierwszy
    przebieg CI w historii projektu wywrócił na tym pięć testów. Atrapa gasi kolor przez
    `TERM=dumb`, bo `NO_COLOR` nie przebija `FORCE_COLOR`; gdyby przyszła wersja `rich`
    zmieniła tę precedencję, ten jeden test powie dlaczego, zamiast dziesięciu innych
    padających na niezrozumiałym braku podnapisu."""
    result = runner.invoke(app, ["pobierz", "--help"], env={"FORCE_COLOR": "1"})

    assert "[" not in result.output, "wyjście CLI w testach musi być bez kolorów"


def test_pobierz_advertises_the_batch_flag(runner: CliRunner) -> None:
    result = runner.invoke(app, ["pobierz", "--help"])

    assert "--partie" in result.output


# ------------------------------------------------------------------- wejście bez argumentów


@pytest.mark.parametrize(
    ("stdin_tty", "stdout_tty", "expected"),
    [(True, True, True), (False, True, False), (True, False, False), (False, False, False)],
    ids=["terminal", "wejscie_z_pliku", "wyjscie_do_pliku", "potok"],
)
def test_the_no_args_dispatch_predicate_requires_a_real_terminal(
    stdin_tty: bool, stdout_tty: bool, expected: bool
) -> None:
    """Predykat, na którym stoi „bez polecenia uruchom kreator”: obie strony muszą być TTY."""
    assert interactive_available(stdin_tty=stdin_tty, stdout_tty=stdout_tty, yes=False) is expected


def test_running_without_arguments_outside_a_terminal_prints_help(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """W CI i w potoku `CliRunner` nie ma TTY — kreator zawisłby, więc pokazujemy pomoc."""
    result = runner.invoke(app, [], env=env)

    assert result.exit_code == 0
    assert "pobierz" in result.output  # spis poleceń, nie pierwsze pytanie kreatora
    assert "Wybierz działanie" not in result.output
    # Pomoc pada raz. `typer` w trybie `rich` drukuje ją sam i zwraca pusty napis, więc
    # wcześniejsze `console.print(ctx.get_help())` dokładało tylko pusty wiersz.
    assert result.output.count("Usage:") == 1


def test_the_wizard_refuses_to_start_without_a_terminal(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Wywołane wprost `kreator` bez TTY ma powiedzieć, czego użyć w harmonogramie."""
    result = runner.invoke(app, ["kreator"], env=env)

    assert result.exit_code == EXIT_CONFIG
    assert "wymaga terminala" in result.output
    assert "--tak" in result.output  # podpowiedź dla harmonogramu


# ----------------------------------------------------------------------------- sprawdz-nip


def test_sprawdz_nip_rejects_a_bad_checksum_before_any_request(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Literówka w NIP-ie kosztuje zero żądań — kontrola jest lokalna."""
    api = counting_api(0)
    patch_transport(monkeypatch, api)

    result = runner.invoke(app, ["sprawdz-nip", "3563457931"], env=env)

    assert result.exit_code == EXIT_CONFIG
    assert "Niepoprawny NIP" in result.output
    assert api.requests == []


def test_sprawdz_nip_reports_a_missing_entry_as_a_normal_result(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Poprawny NIP bez wpisu kończy się kodem 0 i wyjaśnieniem, nie błędem."""
    api = FakeApi()
    api.fallback = lambda request: httpx.Response(204)
    patch_transport(monkeypatch, api)

    result = runner.invoke(app, ["sprawdz-nip", NIP], env=env)

    assert result.exit_code == 0, result.output
    assert f"NIP {NIP}" in result.output
    assert "brak wpisu" in result.output


def test_sprawdz_nip_does_not_write_a_workbook_unless_asked(
    runner: CliRunner, env: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sprawdzenie firmy to podgląd; plik powstaje dopiero z `--out`."""
    api = FakeApi()
    api.fallback = lambda request: httpx.Response(204)
    patch_transport(monkeypatch, api)

    runner.invoke(app, ["sprawdz-nip", NIP], env=env)

    assert not list((tmp_path / "dane" / "wyniki").glob("*.xlsx"))


def test_sprawdz_nip_shows_the_first_screen(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Każde wejście pokazuje ten sam pierwszy ekran — §A nie robi wyjątku dla NIP-u."""
    api = FakeApi()
    api.fallback = lambda request: httpx.Response(204)
    patch_transport(monkeypatch, api)

    result = runner.invoke(app, ["sprawdz-nip", NIP], env=env)

    assert "środowisko" in result.output
    assert "TEST" in result.output


def test_sprawdz_nip_refuses_production_without_consent(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Nowe polecenie musi przechodzić tę samą bramkę zgody co reszta (§B)."""
    result = runner.invoke(app, ["sprawdz-nip", NIP, "-s", "prod", "--tak"], env=env)

    assert result.exit_code == EXIT_CONFIG
    assert "--produkcja" in result.output
    assert not (tmp_path / "dane").exists()


def test_kreator_refuses_production_without_consent(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    result = runner.invoke(app, ["kreator", "-s", "prod"], input="n\n", env=env)

    assert result.exit_code == EXIT_CONFIG
    assert "zgody" in result.output or "--produkcja" in result.output
    assert not (tmp_path / "dane").exists()


# ------------------------------------------------------------- flagi i YAML dają to samo


def cost_table_lines(output: str) -> list[str]:
    """Wiersze tabeli kosztów — do porównania dwóch wejść bez wrażliwości na resztę ekranu."""
    return [
        line.strip()
        for line in output.splitlines()
        if "lista podstawowa" in line or "z pełnymi szczegółami" in line or "Znaleziono" in line
    ]


def test_polecenie_z_kreatora_wraca_do_cli_i_daje_te_sama_tabele_kosztow(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0008 decyzja 2 po wycofaniu pliku zapytania (ADR-0022): pętla ma się domykać.

    Do 2026-09-10 ten test porównywał flagi z plikiem YAML. Plik zniknął, ale gwarancja
    została i jest **mocniejsza**: kreator wypisuje gotowe polecenie, więc to ono musi dać
    dokładnie tę samą tabelę kosztów, co kryteria, z których powstało. Polecenie, które po
    wklejeniu znaczy coś innego niż ekran, na którym padło, byłoby cichym rozjazdem — a to
    jedyne miejsce, gdzie tę pętlę widać w całości.
    """
    criteria = Criteria(miasto=("Białystok",), pkd=("6201Z",))
    polecenie = texts.polecenie_powtarzajace(criteria)
    assert polecenie is not None
    # `ceidg-tool` na początku to nazwa programu — `CliRunner` dostaje samą listę argumentów.
    argumenty = shlex.split(polecenie)[1:]

    patch_transport(monkeypatch, counting_api(1_240))
    z_flag = runner.invoke(
        app, ["pobierz", "-m", "Białystok", "--pkd", "62.01.Z", "--tak"], env=env
    )
    patch_transport(monkeypatch, counting_api(1_240))
    z_polecenia = runner.invoke(app, argumenty, env=env)

    assert z_flag.exit_code == 0, z_flag.output
    assert z_polecenia.exit_code == 0, z_polecenia.output
    assert cost_table_lines(z_flag.output) == cost_table_lines(z_polecenia.output)
    assert any("1 240" in line for line in cost_table_lines(z_flag.output))
    assert "Białystok" in z_polecenia.output


def test_polecenie_z_kreatora_cytuje_wartosci_ze_spacja(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Miejscowość „Stara Łomża przy Szosie" ma przetrwać drogę przez powłokę w jednym kawałku.

    Bez cytowania polecenie rozpadłoby się na trzy argumenty i `--miasto` dostałoby „Stara",
    a `pobierz` — dwa argumenty pozycyjne, których nie przyjmuje. Wartość z rejestru wraca tu
    do wiersza poleceń, więc to jest ta sama klasa wejścia, którą neutralizują `safetext`
    i `richtext`, tylko trzecim kanałem.
    """
    criteria = Criteria(miasto=("Stara Łomża przy Szosie",))
    polecenie = texts.polecenie_powtarzajace(criteria)
    assert polecenie is not None and "'Stara Łomża przy Szosie'" in polecenie

    patch_transport(monkeypatch, counting_api(3))
    wynik = runner.invoke(app, shlex.split(polecenie)[1:], env=env)

    assert wynik.exit_code == 0, wynik.output
    assert "Stara Łomża przy Szosie" in wynik.output


# ----------------------------------------------------------------------------- walidacja flag


def test_an_unknown_source_is_refused_before_any_request(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    api = counting_api(10)
    patch_transport(monkeypatch, api)

    result = runner.invoke(app, ["pobierz", "-m", "Białystok", "--zrodlo", "xxx", "--tak"], env=env)

    assert result.exit_code == EXIT_CONFIG
    assert "Nieznane źródło" in result.output
    assert api.requests == []


def test_an_unknown_format_is_refused_before_fetching_not_after(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zły format wykryty po pobraniu oznaczałby godziny pracy wyrzucone do kosza."""
    api = counting_api(10)
    patch_transport(monkeypatch, api)

    result = runner.invoke(app, ["pobierz", "-m", "Białystok", "--format", "pdf", "--tak"], env=env)

    assert result.exit_code == EXIT_CONFIG
    assert "Nieznany format" in result.output
    assert api.requests == []


# ------------------------------------------------------ rocznik PKD 2007 (ADR-0012)

# Fryzjerstwo: `9621Z` w PKD 2025 ma poprzednika `9602Z`, który wciąga też kosmetykę. Kody
# pochodzą z wygenerowanej tablicy, a te testy jadą po niej **naprawdę** — to jedyne miejsce,
# gdzie flaga, tablica i budowa URL spotykają się w jednym przebiegu.
FRYZJER_2025 = "96.21.Z"
FRYZJER_2007 = "9602Z"


def pkd_w_zadaniach(api: FakeApi) -> set[str]:
    return {kod for url in api.requests for kod in ("9621Z", FRYZJER_2007) if kod in url}


def test_pobierz_advertises_the_vintage_flag(runner: CliRunner) -> None:
    """Flaga, której nie widać w pomocy, nie istnieje dla operatora."""
    result = runner.invoke(app, ["pobierz", "--help"])

    assert "--pkd-2007" in result.output
    assert "--bez-pkd-2007" in result.output


def test_the_noninteractive_run_keeps_todays_population_and_warns(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--tak` bez decyzji ma zachować dzisiejsze zapytanie — i powiedzieć, czego nie obejmuje.

    Harmonogram, który po aktualizacji narzędzia zaczyna zwracać inną populację, cicho zmienia
    cudzy raport. Milczenie o pominiętych firmach byłoby jednak drugą połową tego samego
    defektu, więc zdanie musi paść i musi wskazać flagę (ADR-0012, sub-decyzja 5).
    """
    api = counting_api(1_240)
    patch_transport(monkeypatch, api)

    result = runner.invoke(app, ["pobierz", "--pkd", FRYZJER_2025, "--tak"], env=env)

    assert result.exit_code == 0, result.output
    assert pkd_w_zadaniach(api) == {"9621Z"}  # stary kod nie poleciał
    assert FRYZJER_2007 in result.output and "--pkd-2007" in result.output


def test_the_vintage_flag_actually_widens_the_query_sent_to_the_register(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cała ścieżka na prawdziwej tablicy: flaga → `Criteria.pkd_2007` → parametry URL.

    Sprawdzenie patrzy na wysłane żądania, nie na tekst: „szukamy też po starych kodach”
    jest obietnicą o zapytaniu, a nie o ekranie.
    """
    api = counting_api(1_240)
    patch_transport(monkeypatch, api)

    result = runner.invoke(app, ["pobierz", "--pkd", FRYZJER_2025, "--pkd-2007", "--tak"], env=env)

    assert result.exit_code == 0, result.output
    assert pkd_w_zadaniach(api) == {"9621Z", FRYZJER_2007}


def test_the_explicit_refusal_matches_the_scheduled_default(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--bez-pkd-2007` ma znaczyć dokładnie to, co dziś robi `--tak` — bez niespodzianki."""
    api = counting_api(1_240)
    patch_transport(monkeypatch, api)

    result = runner.invoke(
        app, ["pobierz", "--pkd", FRYZJER_2025, "--bez-pkd-2007", "--tak"], env=env
    )

    assert result.exit_code == 0, result.output
    assert pkd_w_zadaniach(api) == {"9621Z"}


def test_polecenie_niesie_wybor_rocznika_ktory_powtarza_populacje(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Szeroki wybór ma się powtórzyć, choć `--tak` sam z siebie zawęża.

    Nośnikiem jest dziś `--pkd-2007` w poleceniu wypisanym przez kreator, a nie pole
    `pkd_2007` w pliku zapytania (ADR-0022). Różnica, którą ta zamiana wprowadza, jest
    nazwana w `texts.POLECENIE_ROCZNIK` i sprawdzona niżej: plik niósł konkretne kody,
    polecenie niesie decyzję, a kody dobiera tablica przejścia przy uruchomieniu. Dla
    tej samej tablicy populacja jest ta sama — i to jest to, co ten test mierzy.
    """
    szerokie = Criteria(pkd=("9621Z",), pkd_2007=(FRYZJER_2007,))
    polecenie = texts.polecenie_powtarzajace(szerokie)
    assert polecenie is not None and "--pkd-2007" in polecenie
    # Kody 2007 **nie** wchodzą do polecenia jako wartości — nie ma dla nich flagi listowej.
    assert FRYZJER_2007 not in polecenie

    api = counting_api(1_240)
    patch_transport(monkeypatch, api)
    result = runner.invoke(app, shlex.split(polecenie)[1:], env=env)

    assert result.exit_code == 0, result.output
    assert pkd_w_zadaniach(api) == {"9621Z", FRYZJER_2007}


# ------------------------------------------------------------ jedno zdanie, dwa wejścia (reguła 9)


def test_the_update_command_takes_its_sentence_from_the_shared_catalogue(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`aktualizuj` i kreator mówią to samo, bo biorą zdanie z `texts`, a nie każdy swoje.

    Kreatorską kopię pilnuje `tests/test_wizard_menu.py`; tutaj sprawdzamy, że polecenie
    nie ma własnej. Porównujemy z wynikiem funkcji, nie z literałem — literał stoi w jednym
    miejscu i to on jest źródłem obu komunikatów.
    """
    api = FakeApi()
    api.fallback = lambda request: httpx.Response(204)
    patch_transport(monkeypatch, api)

    result = runner.invoke(app, ["aktualizuj", "--tak"], env=env)

    assert result.exit_code == 0
    # pusty zakres nie dochodzi do pobrania, więc zdaniem końcowym jest odmowa startu —
    # też z katalogu, nie z literału w `cli.py`
    assert texts.update_declined(0) in result.output


@pytest.mark.parametrize("command", ["pobierz", "wznow", "aktualizuj"])
def test_the_lock_message_points_at_a_flag_that_exists(runner: CliRunner, command: str) -> None:
    """`store.acquire_lock` odsyła operatora do `--force`; obietnica ma mieć pokrycie.

    Przez pół projektu nie miała: `force_lock` istniało tylko jako argument w `pipeline`,
    więc po ubiciu procesu udokumentowane `wznow` odbijało się o blokadę martwego procesu.
    `aktualizuj` bierze tę samą blokadę, więc dostaje tę samą drogę wyjścia — inaczej komunikat
    obiecywałby flagę, której akurat to polecenie nie ma.
    """
    result = runner.invoke(app, [command, "--help"])

    assert "--force" in result.output


def test_downloading_a_report_from_the_cli_shows_progress(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`raporty --pobierz` też ściąga te 21 MB — i też nie ma prawa milczeć.

    Znalezisko z drugiego przeglądu 2026-09-07: postęp dostała ścieżka `pobierz --zrodlo
    raport`, a to polecenie — reklamowane w README jako osobna droga po ten sam plik —
    zostało z komunikatem, ciszą i „Zapisano". Test pilnuje szwu, a nie wyglądu paska:
    czy klient w ogóle dostał kanał, którym może się odezwać.
    """
    api = FakeApi()
    api.add_fixture("raporty.json")
    api.fallback = lambda request: httpx.Response(200, content=b"PK\x03\x04" + b"x" * 4096)
    patch_transport(monkeypatch, api)
    passed: list[bool] = []
    real = CeidgClient.download_report

    def spy(
        self: CeidgClient,
        report: Report,
        dest: Path,
        *,
        progress: object = None,
    ) -> Path:
        passed.append(progress is not None)
        return real(self, report, dest, progress=progress)  # type: ignore[arg-type]

    monkeypatch.setattr(CeidgClient, "download_report", spy)
    report_id = load_fixture("raporty.json")["body"]["raporty"][0]["id"]

    result = runner.invoke(app, ["raporty", "--pobierz", report_id], env=env)

    assert result.exit_code == 0, result.output
    assert passed == [True], "pobieranie raportu z CLI nie dostało kanału postępu"


def test_a_description_with_yes_refuses_before_asking_the_model(
    runner: CliRunner, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--opis` z `--tak` odmawia **zanim** cokolwiek wyda (znalezisko z przebiegu B5).

    Kryterium przebiegu — kod 3 i właściwy komunikat — było spełnione od początku, ale trwało
    7,4 s i renderowało interpretację: model był pytany, a dopiero potem padała odmowa. Program
    wie od początku, że tej kombinacji nie da się potwierdzić, więc płacenie za tę wiedzę jest
    tą samą pomyłką, co żądanie do API na NIP z błędną sumą kontrolną.
    """
    wywolania: list[str] = []

    class Szpieg:
        def interpret(self, opis: str, **_: object) -> object:  # pragma: no cover - ma nie paść
            wywolania.append(opis)
            raise AssertionError("model zapytany mimo --tak")

    monkeypatch.setattr("ceidg_tool.pipeline._build_assistant", lambda *a, **k: (Szpieg(), None))
    patch_transport(monkeypatch, FakeApi())

    result = runner.invoke(app, ["pobierz", "--opis", "firmy budowlane w Łomży", "--tak"], env=env)

    # `rich` łamie wiersze, więc porównujemy po zwinięciu białych znaków — inaczej asercja
    # zależałaby od szerokości terminala, a nie od treści.
    wyjscie = " ".join(result.output.split())

    assert result.exit_code == 3
    assert "tryb --tak nie podejmuje tej decyzji" in wyjscie
    assert wywolania == [], "model został zapytany mimo --tak"
    assert "Interpretacja" not in wyjscie
