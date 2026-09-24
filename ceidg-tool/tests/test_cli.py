"""Warstwa CLI — zgoda na produkcję i szczelność tokenu. Offline: żaden test nie tworzy klienta.

Produkcja to jedyne miejsce, gdzie narzędzie dotyka prawdziwych danych osobowych
(UZUPELNIENIE_01 §B). Zgoda jest argumentem, nie flagą globalną, więc każda droga do
`--srodowisko prod` musi kończyć się odmową, zanim powstanie klient HTTP i baza.
"""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path

import pytest
from rich.console import Console
from typer import rich_utils
from typer.testing import CliRunner

import ceidg_tool.config as config
from ceidg_tool import cli
from ceidg_tool.cli import app
from ceidg_tool.console import ConsoleEvents, LineEvents
from ceidg_tool.criteria import Criteria
from ceidg_tool.errors import ProdWithoutConsentError
from ceidg_tool.pipeline import build_deps
from ceidg_tool.ui import flow
from tests.support import zarejestrowane_polecenia

TOKEN = "token-testowy-nie-jwt"
EXIT_CONFIG = 3


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    """CLI w pustym katalogu roboczym, bez magazynu haseł i bez `.env` projektu."""
    monkeypatch.chdir(tmp_path)
    # `*_` bo funkcja bierze teraz nazwę pozycji w magazynie (token CEIDG albo klucz
    # asystenta). Atrapa bez tego parametru wywracała **każdy** test CLI naraz.
    monkeypatch.setattr(config, "read_token_from_keyring", lambda *_: None)
    # Szerokość konsoli przypięta, żeby asercje o **treści** komunikatów nie mierzyły przy
    # okazji szerokości maszyny — patrz `tests/test_cli_phase3.py`, gdzie ten sam brak wywrócił
    # pięć testów na pierwszym w historii przebiegu CI. Konsola aplikacji powstaje na poziomie
    # modułu, więc samo `COLUMNS` już jej nie dosięga.
    monkeypatch.setenv("COLUMNS", "200")
    # Kolor wyłączony, bo `rich` koloruje **nazwę opcji**, rozbijając ją sekwencjami ANSI:
    # przy włączonym kolorze `"--partie" in result.output` jest fałszem, choć flaga jest na
    # ekranie. Na GitHub Actions kolor jest domyślnie włączony, więc pierwszy przebieg CI
    # wywrócił na tym pięć testów zielonych lokalnie. `NO_COLOR` nie przebija `FORCE_COLOR`
    # w tej wersji `rich`, `TERM=dumb` przebija — i dlatego jest tu jeszcze test-strażnik.
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setattr(cli.console, "width", 200)
    # …i to **nie wystarcza dla `--help`**. Pomocy nie rysuje `cli.console`, tylko własna
    # konsola typera (`typer.rich_utils._get_rich_console`), która przy `MAX_WIDTH is None`
    # wykrywa terminal sama: na Windowsie szeroko, na Linuksie 80 kolumn. Dwa przebiegi CI
    # były przez to czerwone (2026-09-08), a komentarz wyżej przez cały ten czas twierdził,
    # że sprawa jest załatwiona — bo nikt nie sprawdził, która konsola rysuje `--help`.
    #
    # Uczciwie o tej linijce: **nie jest nośna dla asercji**. Sprawdzone mutacją — jej
    # usunięcie zostawia testy zielone, bo porównania idą przez `_bez_lamania`, które nie
    # zależy od szerokości. Zostaje po to, żeby renderowanie było powtarzalne między
    # Windowsem a Linuksem, a nie po to, żeby cokolwiek gwarantować.
    monkeypatch.setattr(rich_utils, "MAX_WIDTH", 200)
    return CliRunner()


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return {"CEIDG_TOKEN": TOKEN, "CEIDG_DATA_DIR": str(tmp_path / "dane")}


def test_prod_in_noninteractive_mode_requires_explicit_flag(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """`--tak` przyjmuje decyzje domyślne, ale nigdy nie domyśla się zgody na produkcję."""
    result = runner.invoke(app, ["pobierz", "-w", "podlaskie", "-s", "prod", "--tak"], env=env)

    assert result.exit_code == EXIT_CONFIG
    assert "--produkcja" in result.output
    assert not (tmp_path / "dane").exists()  # nic nie powstało: ani baza, ani logi


def test_prod_prompt_declined_stops_before_any_request(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    result = runner.invoke(app, ["pobierz", "-w", "podlaskie", "-s", "prod"], input="n\n", env=env)

    assert result.exit_code == EXIT_CONFIG
    assert "PRODUKCJI" in result.output  # pytanie padło
    assert "wymaga jawnej zgody" in result.output
    assert not (tmp_path / "dane").exists()


@pytest.mark.parametrize(
    "command",
    [
        ["pobierz", "-w", "podlaskie", "-s", "prod", "--tak"],
        ["wznow", "-s", "prod", "--tak"],
        ["eksportuj", "-s", "prod"],
        ["runy", "-s", "prod"],
        ["aktualizuj", "-s", "prod", "--tak"],
        ["wyczysc", "-s", "prod", "--tak"],
        ["sprawdz-token", "-s", "prod"],
    ],
)
def test_every_command_refuses_prod_without_consent(
    runner: CliRunner, env: dict[str, str], command: list[str], tmp_path: Path
) -> None:
    result = runner.invoke(app, command, input="n\n", env=env)

    assert result.exit_code == EXIT_CONFIG, result.output
    assert "zgody" in result.output or "--produkcja" in result.output
    assert not (tmp_path / "dane").exists()


def test_consent_flag_lets_the_read_only_token_check_reach_prod(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """`--produkcja` przechodzi bramkę zgody; `sprawdz-token` nie wysyła żadnego żądania."""
    result = runner.invoke(app, ["sprawdz-token", "-s", "prod", "--produkcja"], env=env)

    assert result.exit_code == 0
    assert "Środowisko: prod" in result.output


def test_default_environment_is_test_without_any_flag(
    runner: CliRunner, env: dict[str, str]
) -> None:
    result = runner.invoke(app, ["sprawdz-token"], env=env)

    assert result.exit_code == 0
    assert "Środowisko: test" in result.output


def test_token_value_never_reaches_cli_output(runner: CliRunner, env: dict[str, str]) -> None:
    """Widoczny jest skrót i źródło tokenu, nigdy sama wartość (§B)."""
    result = runner.invoke(app, ["sprawdz-token"], env=env)

    assert TOKEN not in result.output
    assert config.token_fingerprint(TOKEN) in result.output
    assert "Źródło tokenu: env" in result.output


def test_empty_criteria_are_refused_before_the_first_request(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Bez filtra zapytanie objęłoby cały rejestr — CLI odmawia zamiast pytać API o `count`."""
    result = runner.invoke(app, ["pobierz", "--tak"], env=env)

    assert result.exit_code == EXIT_CONFIG
    assert "przynajmniej jedno kryterium" in result.output


def test_invalid_criteria_are_reported_as_configuration_error(
    runner: CliRunner, env: dict[str, str]
) -> None:
    result = runner.invoke(app, ["pobierz", "-w", "nieistniejace", "--tak"], env=env)

    assert result.exit_code == EXIT_CONFIG
    assert "Niepoprawne kryteria" in result.output


def test_prod_consent_is_an_argument_not_a_global_flag() -> None:
    """Kontrakt, na którym opierają się testy CLI: zgoda przechodzi przez `resolve_environment`."""
    assert config.resolve_environment("prod", prod_consent=True) == "prod"
    with pytest.raises(ProdWithoutConsentError):
        config.resolve_environment("prod", prod_consent=False)


def test_the_production_prompt_understands_a_polish_yes(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """„tak" ma przechodzić bramkę zgody tak samo jak „y".

    Bez kryteriów polecenie zatrzymuje się zaraz po zgodzie i przed jakimkolwiek żądaniem,
    więc test dowodzi wyłącznie tego, że odpowiedź została zrozumiana: gdyby „tak" wciąż
    znaczyło „nie", komunikat mówiłby o zgodzie na produkcję, a nie o brakujących kryteriach.
    """
    result = runner.invoke(app, ["pobierz", "-s", "prod"], input="tak\n", env=env)

    assert result.exit_code == EXIT_CONFIG
    assert "Podaj przynajmniej jedno kryterium" in result.output
    assert "wymaga jawnej zgody" not in result.output


def test_the_production_prompt_understands_a_polish_no(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """„nie" zatrzymuje przed produkcją — wcześniej `typer.confirm` też, ale przypadkiem:
    każda odpowiedź poza `y` znaczyła „nie", więc „tak" również."""
    result = runner.invoke(
        app, ["pobierz", "-w", "podlaskie", "-s", "prod"], input="nie\n", env=env
    )

    assert result.exit_code == EXIT_CONFIG
    assert "wymaga jawnej zgody" in result.output
    assert not (tmp_path / "dane").exists()


def test_bare_enter_at_the_production_prompt_is_not_consent(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Sam Enter to odpowiedź domyślna, a domyślną jest odmowa — bez tego testu odwrócenie
    wartości domyślnej w `_confirm` przechodziło przez cały zestaw."""
    result = runner.invoke(app, ["pobierz", "-w", "podlaskie", "-s", "prod"], input="\n", env=env)

    assert result.exit_code == EXIT_CONFIG
    assert "wymaga jawnej zgody" in result.output
    assert not (tmp_path / "dane").exists()


# ------------------------------------------------- flagi kryteriów (parytet z `Criteria`)

# Pola `Criteria`, które **nie** mają być zwykłą flagą listową, i powód dla każdego:
POLA_BEZ_WLASNEJ_FLAGI_LISTOWEJ = {
    # Rocznik przejściowy nie jest wyborem kodów, tylko decyzją „szukać też po starych" —
    # stąd flaga trójstanowa `--pkd-2007/--bez-pkd-2007`, a nie lista kodów (ADR-0012).
    "pkd_2007",
    "data_od",  # `--od`
    "data_do",  # `--do`
    "szczegoly",  # `--szczegoly/--lista`
    "max_rekordow",  # `--maks`
}


def _opcje_polecenia(nazwa: str) -> set[str]:
    """Nazwy parametrów Pythona przyjmowanych przez polecenie — z typera, nie z siatki nazw."""
    funkcje = [c.callback for c in app.registered_commands if c.callback is not None]
    polecenie = next(f for f in funkcje if (f.__name__.replace("_", "-")) == nazwa)
    return set(inspect.signature(polecenie).parameters)


def test_kazde_pole_kryteriow_ma_flage_w_wierszu_polecen() -> None:
    """Pole `Criteria` bez flagi jest po wycofaniu pliku zapytania (ADR-0022) nieosiągalne
    z wiersza poleceń w ogóle — zostaje mu asystent, czyli klucz API i jedno zdanie prozy.

    Tak było do 2026-09-10 z `imie`, `nazwisko`, `ulica` i `kod`: filtr istniał, przechodził
    walidację, jechał do API — a operator wiersza poleceń nie miał jak go podać i nie miał skąd
    się o nim dowiedzieć, bo `--help` go nie wymieniał. Test porównuje **spisy**, więc następne
    dopisane pole albo dostanie flagę, albo trafi na listę wyjątków wraz z powodem. Od
    wycofania pliku zapytania ten test jest jedynym strażnikiem kompletności wejścia CLI.
    """
    brakujace = (
        set(Criteria.model_fields) - POLA_BEZ_WLASNEJ_FLAGI_LISTOWEJ - _opcje_polecenia("pobierz")
    )

    assert brakujace == set()
    # Kontrola odwrotna: wyjątek wpisany „na zapas" dla pola, którego nie ma, ukrywałby braki.
    assert POLA_BEZ_WLASNEJ_FLAGI_LISTOWEJ <= set(Criteria.model_fields)


@pytest.mark.parametrize(
    ("flaga", "wartosc", "pole", "oczekiwane"),
    [
        ("--imie", "Marek", "imie", ("Marek",)),
        ("--nazwisko", "Nowak", "nazwisko", ("Nowak",)),
        ("--ulica", "Kwiatowa", "ulica", ("Kwiatowa",)),
        ("--kod", "15-333", "kod", ("15-333",)),
        ("--budynek", "12A", "budynek", ("12A",)),
        ("--lokal", "3", "lokal", ("3",)),
        ("--nip-sc", "356-345-79-32", "nip_sc", ("3563457932",)),
        ("--regon-sc", "618155359", "regon_sc", ("618155359",)),
    ],
)
def test_nowa_flaga_dociera_do_kryteriow(
    flaga: str, wartosc: str, pole: str, oczekiwane: tuple[str, ...]
) -> None:
    """Flaga w `--help` bez połączenia z `Criteria` byłaby filtrem, który nic nie filtruje."""
    kryteria = cli._criteria_from_options({pole: [wartosc]})

    assert getattr(kryteria, pole) == oczekiwane
    assert flaga.lstrip("-").replace("-", "_") == pole


def test_pomoc_wymienia_nowe_flagi(runner: CliRunner) -> None:
    """`--help` jest jedynym miejscem, gdzie operator wiersza poleceń widzi, co da się podać."""
    pomoc = runner.invoke(app, ["pobierz", "--help"]).output

    for flaga in ("--imie", "--nazwisko", "--ulica", "--kod", "--budynek", "--lokal"):
        assert flaga in pomoc, f"brak {flaga} w pomocy"
    assert "--nip-sc" in pomoc and "--regon-sc" in pomoc


def test_zla_wartosc_w_fladze_wraca_zdaniem_po_polsku(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Błąd walidacji ma być zdaniem, nie zrzutem pydantica — jak w kreatorze i u asystenta.

    Do 2026-09-10 wiersz poleceń był jedynym z czterech wejść do `Criteria`, które oddawało
    surowe `[type=value_error, input_value=…]` z odnośnikiem do errors.pydantic.dev.
    """
    zly_kod = runner.invoke(app, ["pobierz", "--kod", "15333", "--tak"], env=env)
    zla_data = runner.invoke(
        app, ["pobierz", "-w", "podlaskie", "--od", "2020-13-01", "--tak"], env=env
    )

    assert zly_kod.exit_code == EXIT_CONFIG
    assert "15-333" in zly_kod.output and "pydantic" not in zly_kod.output
    assert zla_data.exit_code == EXIT_CONFIG
    assert "RRRR-MM-DD" in zla_data.output and "isoformat" not in zla_data.output


# --- kto buduje asystenta (ADR-0025, decyzja 2) --------------------------------------------

BUDUJE_ASYSTENTA: dict[str, bool] = {
    # Jedyne dwie ścieżki dochodzące do `flow.collect_from_description`.
    "kreator": True,
    "pobierz": True,  # tylko z `--opis`; osobny test pilnuje, że bez niego nie buduje
    # Reszta nie ma jak go użyć, więc nie ma za co płacić: import SDK, słownik 728 pozycji,
    # prompt, klient HTTP do drugiego hosta i `_wycisz_sdk()` grzebiące w `os.environ`.
    "sprawdz-nip": False,
    # Nie buduje `Deps` w ogóle: dwa pliki YAML i czysta funkcja (ADR-0026, decyzja 5).
    "szukaj-pkd": False,
    "wznow": False,
    "raporty": False,
    "aktualizuj": False,
    "eksportuj": False,
    "runy": False,
    "wyczysc": False,
    "sprawdz-token": False,
    "token": False,
}


def test_tabela_budowy_asystenta_obejmuje_wszystkie_polecenia() -> None:
    """Połowa kompletności: polecenie, które ominęło regułę, ma być widoczne.

    Sama tabela porównywana z rzeczywistością nie widzi polecenia, którego w niej nie ma —
    a to jest właśnie ten przypadek, w którym asystent dochodzi po cichu do ścieżki, która
    nigdy go nie potrzebowała. Ta sama sztuczka co przy `POLA_BEZ_WLASNEJ_FLAGI_LISTOWEJ`.
    """
    assert set(BUDUJE_ASYSTENTA) == zarejestrowane_polecenia()


def _prosba_o_asystenta(nazwa: str) -> bool:
    """Czy polecenie prosi `build_deps` o asystenta — odczytane ze składni `cli.py`.

    Skan, a nie uruchomienie: każde polecenie wymagałoby innego zestawu poprawnych argumentów,
    a pytanie jest o **kod**, nie o przebieg. Ten sam wybór narzędzia co w `test_boundaries.py`.

    Prośbą jest każdy `asystent=` różny od literału `"nieproszony"`. Dziś oba polecenia podają
    zmienną (`tryb_asystenta`), więc skan czyta ją jako prośbę i ma rację: `"buduj"` jest w jej
    dziedzinie. Brak argumentu znaczy „ta ścieżka nie ma jak go użyć".

    Warunek mówił „różny od literału `False`" do 2026-09-23, kiedy para booleanów ustąpiła
    jednemu `Asystent` (przegląd części A). Sam warunek przeżyłby zmianę zielony — `False` po
    prostu przestałoby się pojawiać — i **każdy** `asystent=` liczyłby się jako prośba, łącznie
    z jawnym `asystent="nieproszony"` w nowym poleceniu. Zielony test po zmianie typu nie znaczy,
    że test nadal pyta o to samo.

    Dwie granice tego skanu, nazwane, bo niewypowiedziana granica czyta się jak jej brak
    (przegląd 2026-09-23). Widzi wyłącznie `build_deps`/`_demo_deps` wołane **wprost w ciele
    polecenia**: delegacja do pomocnika przeczyta się jako „nie prosi" przy prawdziwym
    `asystent=True`. I dopasowuje `FunctionDef` po nazwie polecenia, więc podpolecenia grupy
    `token` (`token zapisz`, `token usun`) są poza jego zasięgiem — dziś bez ryzyka, bo żadne
    z nich nie buduje `Deps`, ale połowa kompletności tam nie sięga.
    """
    zrodlo = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    for wezel in ast.walk(zrodlo):
        if not isinstance(wezel, ast.FunctionDef):
            continue
        if wezel.name.replace("_", "-") != nazwa:
            continue
        for wywolanie in ast.walk(wezel):
            if not isinstance(wywolanie, ast.Call):
                continue
            cel = wywolanie.func
            if not isinstance(cel, ast.Name) or cel.id not in ("build_deps", "_demo_deps"):
                continue
            for kw in wywolanie.keywords:
                if kw.arg == "asystent" and not (
                    isinstance(kw.value, ast.Constant) and kw.value.value == "nieproszony"
                ):
                    return True
    return False


@pytest.mark.parametrize("polecenie", sorted(BUDUJE_ASYSTENTA))
def test_asystent_budowany_tylko_tam_gdzie_da_sie_go_uzyc(polecenie: str) -> None:
    """Deklaracja porównana z tym, o co polecenie naprawdę prosi w kodzie.

    Czerwienieje w obie strony: gdy nowe polecenie dostanie asystenta po cichu i gdy refaktor
    odbierze go `pobierz --opis`. Druga strona jest tą mniej oczywistą — asystent, który
    przestał powstawać, nie wywraca niczego głośno, tylko zamienia opis zdaniem w odmowę.
    """
    assert _prosba_o_asystenta(polecenie) is BUDUJE_ASYSTENTA[polecenie]


def test_pobierz_nie_buduje_asystenta_bez_opisu(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`pobierz` prosi warunkowo, a tabela zna tylko „prosi / nie prosi".

    Warunek jest tym, co czyni A2 opłacalnym: `pobierz` bez `--opis` to najczęstsze wywołanie
    tego narzędzia i nie ma powodu, żeby otwierało uwierzytelnioną drogę do drugiego hosta.

    Sprawdzane zachowaniem, nie podciągiem źródła. Pierwsza wersja asertowała obecność napisu
    `chce_asystenta = opis is not None` w `cli.py` — a to jest twierdzenie o formatowaniu:
    `ruff format` albo dopisanie członu do warunku przepuszczało ją przy zmienionym zachowaniu
    (przegląd 2026-09-23). Świadkiem jest `ANTHROPIC_LOG`, bo `_wycisz_sdk()` kasuje tę zmienną
    i tylko wtedy, gdy asystent naprawdę powstał.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "x" * 24)
    monkeypatch.setenv("ANTHROPIC_LOG", "debug")

    wynik = runner.invoke(app, ["pobierz", "--demo", "-w", "wielkopolskie", "--maks", "1", "--tak"])

    assert wynik.exit_code == 0, wynik.output
    assert os.environ.get("ANTHROPIC_LOG") == "debug"


def test_polecenie_bez_asystenta_nie_grzebie_w_srodowisku_procesu(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Druga połowa zarzutu z ADR-0025 — ta, której nie widać w 0,2 s.

    `assistant.caller._wycisz_sdk()` robi `os.environ.pop("ANTHROPIC_LOG", None)`, żeby SDK nie
    logowało opcji żądań i nagłówków poza naszym maskowaniem. Słuszne tam, gdzie SDK pracuje —
    i nie do obrony w `raporty`, które modelu nie zapyta, a mimo to kasowało operatorowi
    ustawienie diagnostyczne, bo asystent powstawał przy każdym poleceniu online.

    Bez tego testu pozostałaby z tego zarzutu sama liczba, a liczba jest jego mniejszą połową.
    Test był czerwony przed A2 i to jest jego cała treść.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "x" * 24)
    monkeypatch.setenv("ANTHROPIC_LOG", "debug")

    wynik = runner.invoke(app, ["sprawdz-nip", "3563457932", "--demo"])

    # Kod wyjścia **przed** właściwą asercją: pierwsza wersja tego testu wołała `raporty
    # --demo`, a `raporty` nie ma flagi `--demo` — więc wywołanie kończyło się błędem użycia,
    # nic się nie budowało i test przechodził, nie sprawdzając niczego. Bez tej linijki
    # przeszedłby też po usunięciu polecenia.
    assert wynik.exit_code == 0, wynik.output
    assert os.environ.get("ANTHROPIC_LOG") == "debug"


# --- --bez-asystenta (ADR-0025, decyzja 1) -------------------------------------------------


def test_opis_z_bez_asystenta_jest_odmowa_i_nic_nie_powstaje(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Para flag nie do spełnienia — odmowa **przed** zbudowaniem czegokolwiek.

    Ciche zignorowanie jednej z nich jest tą samą pomyłką co żądanie do modelu na
    interpretację, której nikt nie potwierdzi (przebieg B5). Asercja o nieistniejącym katalogu
    danych jest tu nośna: odmowa po `_settings()` zdążyłaby założyć katalog i plik logu, a poza
    tym dałaby **inne** zdanie, gdyby akurat brakowało tokenu.
    """
    monkeypatch.setenv("CEIDG_DATA_DIR", str(tmp_path / "dane"))

    wynik = runner.invoke(app, ["pobierz", "--opis", "firmy w Poznaniu", "--bez-asystenta"])

    assert wynik.exit_code == 3
    assert "wykluczają się" in wynik.output
    assert not (tmp_path / "dane").exists()


def test_bez_asystenta_nie_zmienia_przebiegu_a_zmienia_pierwszy_ekran(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flaga zmienia **jedną** rzecz na ekranie i nic w zachowaniu.

    ADR-0025 obiecywał „bajtowo to samo, co ścieżka bez klucza" — i to była obietnica o jedno
    słowo za mocna, bo decyzja 1 celowo daje fladze własny wiersz §A. Prawdziwe jest zdanie
    słabsze i to ono jest tu sprawdzane: przebieg bez zmian, wiersz §A inny niż przy braku
    klucza, bo operator ma zobaczyć swoją decyzję, a nie stan magazynu haseł.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "x" * 24)
    polecenie = ["pobierz", "--demo", "-w", "wielkopolskie", "--maks", "1", "--tak"]

    z_flaga = runner.invoke(app, [*polecenie, "--bez-asystenta"])
    bez_flagi = runner.invoke(app, polecenie)

    assert z_flaga.exit_code == bez_flagi.exit_code == 0
    assert "--bez-asystenta" in z_flaga.output
    assert "--bez-asystenta" not in bez_flagi.output
    assert "api.anthropic.com" in bez_flagi.output


def test_kreator_pod_flaga_naprawde_nie_buduje_asystenta(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flaga w `kreator` — sprawdzana przez **uruchomienie kreatora**, nie przez odtworzenie stanu.

    Trzecia wersja tego testu, i dwie poprzednie były fałszywie zielone z dwóch różnych powodów.
    Pierwsza wołała `kreator` przez `CliRunner`, który nie ma TTY, więc polecenie odmawiało
    z zasady i asercja mierzyła treść odmowy. Druga — poprawka tamtej — budowała `Deps` ręcznie
    przez `_demo_deps(asystent=False, wylaczony=True)` i sprawdzała `texts` na tak ustawionym
    stanie. Stan był prawdziwy, ale **jedynym producentem tego stanu jest `kreator`**, więc test
    podstawiał za produkcję dokładnie to, czego produkcja miała dowieść: wycięcie flagi
    z `kreator` do zera zostawiało suitę zieloną (przegląd testów 2026-09-23, mutacje M04 i M05).

    Trzecia wersja podmienia dwie rzeczy i tylko te dwie: predykat terminala, bo `CliRunner` go
    nie ma, i `wizard.run_wizard`, żeby zapisać `Deps` zamiast prowadzić dialog. Wszystko między
    flagą a `Deps` zostaje prawdziwe.
    """
    from ceidg_tool.ui import wizard

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "x" * 24)
    monkeypatch.setenv("CEIDG_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "interactive_available", lambda **_: True)
    zapisane: list[object] = []

    def rejestrator(deps: object, *args: object, **kwargs: object) -> int:
        zapisane.append(deps)
        return 0

    monkeypatch.setattr(wizard, "run_wizard", rejestrator)

    wynik = runner.invoke(app, ["kreator", "--demo", "--bez-asystenta"])

    assert wynik.exit_code == 0, wynik.output
    assert len(zapisane) == 1
    deps = zapisane[0]
    assert deps.assistant is None  # type: ignore[attr-defined]
    assert deps.assistant_brak.powod == "WYLACZONY_FLAGA"  # type: ignore[attr-defined]
    # Wiersz §A rysuje `flow.show_first_screen` **w środku** kreatora, więc tu go nie ma —
    # ten ekran ma własny test w `tests/test_ui_flow.py`, który go wywołuje wprost.


def test_kreator_bez_flagi_buduje_asystenta(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Druga strona tej samej pary: bez flagi asystent **ma** powstać.

    Bez tego testu mutacja „`kreator` nigdy nie prosi o asystenta" byłaby niewidoczna, a jest
    to cichsza awaria niż nadmiar: opis zdaniem zamienia się w odmowę, i nic nie krzyczy.
    """
    from ceidg_tool.ui import wizard

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "x" * 24)
    monkeypatch.setenv("CEIDG_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "interactive_available", lambda **_: True)
    zapisane: list[object] = []

    def rejestrator(deps: object, *args: object, **kwargs: object) -> int:
        zapisane.append(deps)
        return 0

    monkeypatch.setattr(wizard, "run_wizard", rejestrator)

    wynik = runner.invoke(app, ["kreator", "--demo"])

    assert wynik.exit_code == 0, wynik.output
    assert zapisane[0].assistant is not None  # type: ignore[attr-defined]


def test_opis_z_tak_odmawia_zanim_zbuduje_asystenta(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Odmowa `--opis --tak` stała **pod** `build_deps` — czyli po zapłaceniu za asystenta.

    Ta para zawsze kończy się kodem 3, więc jest dokładnie „poleceniem, które nie ma jak go
    użyć" — a mimo to płaciło 955 ms, import SDK, klienta z poświadczeniem do api.anthropic.com
    i `_wycisz_sdk()` w `os.environ`. Trafiało to w najgorszą klasę wywołań: harmonogram
    z kluczem w `.env`, chodzący bez nadzoru. Przebieg B5 zamknął tę samą pomyłkę o warstwę
    niżej (nie wydawaj na dowiedzenie się rzeczy wiadomej od razu); tutaj wróciła przy A2,
    bo `chce_asystenta` nie znało `tak`. Znalezisko z przeglądu kodu 2026-09-23.

    Świadkiem jest `ANTHROPIC_LOG`, bo `_wycisz_sdk()` kasuje tę zmienną i tylko wtedy, gdy
    asystent naprawdę powstał; katalog danych mierzy drugą połowę — odmowę przed `_settings()`.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "x" * 24)
    monkeypatch.setenv("ANTHROPIC_LOG", "debug")
    monkeypatch.setenv("CEIDG_DATA_DIR", str(tmp_path / "dane"))
    # Token **musi** tu być. Pierwsza wersja tego testu go nie ustawiała, więc polecenie kończyło
    # się kodem 3 na braku tokenu, nie dochodząc do budowy — i przechodziło także po cofnięciu
    # poprawki, którą miało chronić. Czwarty fałszywie zielony test w tej sesji i ten sam
    # mechanizm co w trzech poprzednich: asercja mierzyła inne zdarzenie niż nazwa testu.
    monkeypatch.setenv("CEIDG_TOKEN", "token-testowy-nie-jwt")

    wynik = runner.invoke(app, ["pobierz", "--opis", "firmy w Poznaniu", "--tak"])

    assert wynik.exit_code == 3
    # Treść odmowy, nie sam kod: kod 3 niesie też brak tokenu i złe kryteria.
    assert "--tak nie podejmuje tej decyzji" in wynik.output
    assert os.environ.get("ANTHROPIC_LOG") == "debug"
    assert not (tmp_path / "dane").exists()


def test_pusty_opis_nie_buduje_asystenta(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--opis ""` przechodziło `opis is not None`, a `if opis:` niżej i tak go nie używało.

    Budowa bez odbiorcy — ten sam rachunek co wyżej, tylko wywołana pustym napisem zamiast
    parą flag.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "x" * 24)
    monkeypatch.setenv("ANTHROPIC_LOG", "debug")

    runner.invoke(app, ["pobierz", "--demo", "--opis", "", "-w", "wielkopolskie", "--maks", "1"])

    assert os.environ.get("ANTHROPIC_LOG") == "debug"


def test_wejscie_bez_podpolecenia_zna_flage_wylacznika() -> None:
    """`python -m ceidg_tool` bez podpolecenia uruchamia kreator — udokumentowane wejście.

    Kreator jest jedyną ścieżką budującą asystenta **zawsze**, więc to jest miejsce, w którym
    wyłącznik jest najbardziej potrzebny; do 2026-09-23 `ceidg-tool --bez-asystenta` kończyło
    się „No such option" i kodem 2 od typera.
    """
    assert "bez_asystenta" in inspect.signature(cli.main).parameters


# --- podział strumieni (ADR-0024, decyzja 2) ------------------------------------------------


def test_blad_idzie_na_stderr_a_stdout_zostaje_pusty(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Nieudane polecenie nie zostawia na stdout ani jednego znaku.

    To jest cała treść decyzji 2: stdout niesie kopertę JSON albo nic, więc wołający może go
    podać parserowi bez filtrowania. Do 2026-09-23 komunikat błędu szedł na stdout
    (`_fail` → `ConsoleView.error` → konsola bez `file=`, czyli `sys.stdout`), a stderr był
    pusty — dokładnie odwrotnie.

    Asercja idzie po `result.stdout`, **nie** po `result.output`: `output` jest strumieniem
    mieszanym (`typer/testing.py:45-60`), więc na nim ten podział byłby nie do odróżnienia od
    jego braku. Test przechodzący na `output` nie sprawdzałby niczego.
    """
    wynik = runner.invoke(
        app, ["pobierz", "--demo", "--tak", "-w", "wielkopolskie", "--format", "xml"], env=env
    )

    assert wynik.exit_code == EXIT_CONFIG
    assert wynik.stdout == ""
    assert "Błąd" in wynik.stderr


def test_ekran_udanego_przebiegu_tez_omija_stdout(runner: CliRunner, env: dict[str, str]) -> None:
    """Nie tylko błędy — podsumowanie, tabela kosztów i oznaki życia również.

    Osobny test, bo to jest inna droga przez program: `_fail` pisze jednym wywołaniem, a udany
    przebieg pisze kilkunastoma, w tym przez odbiorcę zdarzeń.

    Docstring mówił do 2026-09-24, że ten test zobaczyłby **drugą konsolę zbudowaną dla
    pasków**. Nie zobaczyłby: pod `CliRunner` `console.is_terminal` jest fałszem, więc
    `_events()` buduje `LineEvents`, a `ConsoleEvents` w ogóle nie powstaje. Tamtej gwarancji
    pilnuje `test_oba_odbiorce_dziela_konsole_programu[True]` i to on zapala się na mutacji
    wstawiającej własną konsolę. Zdanie nazywające nie tego strażnika jest gorsze niż jego
    brak, bo następna osoba zmieni ten test zamiast tamtego.
    """
    wynik = runner.invoke(
        app,
        ["pobierz", "--demo", "--tak", "-w", "wielkopolskie", "--maks", "5"],
        env=env,
    )

    assert wynik.exit_code == 0, wynik.output
    assert wynik.stdout == ""
    # Pierwszy ekran i ostatni — czyli oba końce przebiegu, a nie jeden zapis, który mógłby
    # trafić na stderr przypadkiem.
    assert "TRYB POKAZU" in wynik.stderr
    assert "Podsumowanie" in wynik.stderr


# --- kto dostaje pasek, a kto wiersze (ADR-0024, decyzja 4) ---------------------------------


@pytest.mark.parametrize(
    ("terminal", "oczekiwany"),
    [(True, ConsoleEvents), (False, LineEvents)],
)
def test_pasek_tylko_na_terminalu_reszta_dostaje_wiersze(
    monkeypatch: pytest.MonkeyPatch, terminal: bool, oczekiwany: type
) -> None:
    """Wybór idzie po `console.is_terminal`, **nie** po `--wynik json`.

    To jest cała decyzja 4 w jednej asercji. Gdyby przełącznikiem była flaga, zmierzona cisza
    (`rich` nie emituje klatek pośrednich poza terminalem) zostałaby otwarta dla wszystkich,
    którzy agentami nie są — każdego harmonogramu i każdego `pobierz > log.txt`. Test bierze
    flagę pod uwagę przez to, że jej **nie** podaje: rozstrzyga sam strumień.
    """
    monkeypatch.setattr(cli, "console", Console(force_terminal=terminal))

    assert isinstance(cli._events(), oczekiwany)


@pytest.mark.parametrize("terminal", [True, False])
def test_oba_odbiorce_dziela_konsole_programu(
    monkeypatch: pytest.MonkeyPatch, terminal: bool
) -> None:
    """Jedna konsola, nie dwie — inaczej pasek wylądowałby na stdout mimo decyzji 2.

    Obie gałęzie, bo własna konsola w którejkolwiek z nich psuje podział strumieni tak samo,
    a domyślny argument `console=None` w obu klasach czyni to pominięcie łatwym.
    """
    podstawiona = Console(force_terminal=terminal)
    monkeypatch.setattr(cli, "console", podstawiona)

    odbiorca = cli._events()

    assert isinstance(odbiorca, ConsoleEvents | LineEvents)
    assert odbiorca.console is podstawiona


def test_pobierz_z_opisem_naprawde_buduje_asystenta(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Druga strona warunku w `pobierz` — i luka, którą znalazła mutacja, nie przegląd.

    `_prosba_o_asystenta` czyta **składnię** wywołania, a `pobierz` podaje zmienną, więc skan
    widzi „prosi" niezależnie od tego, co ta zmienna zawiera. Mutacja ustawiająca ją na stałe
    `"nieproszony"` przechodziła całą suitę zieloną (zmierzone 2026-09-23, 1464 testy), choć
    zamieniała `--opis` w odmowę: `flow.collect_from_description` zastałoby `deps.assistant`
    puste i rzuciło `ASYSTENT_BEZ_POWODU`.

    Świadek jest więc za zmienną, nie przed nią: podstawiony odbiorca zagląda do `Deps`, które
    naprawdę powstało. Ta sama konstrukcja co przy kreatorze, i z tego samego powodu — awaria
    „asystent nie powstał" jest cichsza niż nadmiar.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "x" * 24)
    monkeypatch.setenv("CEIDG_DATA_DIR", str(tmp_path))
    zapisane: list[object] = []

    def rejestrator(deps: object, *args: object, **kwargs: object) -> Criteria:
        zapisane.append(deps)
        return Criteria(wojewodztwo=("wielkopolskie",), max_rekordow=1)

    monkeypatch.setattr(flow, "collect_from_description", rejestrator)

    wynik = runner.invoke(app, ["pobierz", "--demo", "--opis", "fryzjerzy w Poznaniu"])

    assert wynik.exit_code == 0, wynik.output
    assert zapisane and zapisane[0].assistant is not None  # type: ignore[attr-defined]


def test_wylacznik_zostawia_slad_takze_bez_sieci(tmp_path: Path) -> None:
    """Znacznik `WYLACZONY_FLAGA` nie zależy od tego, czy powstał klient HTTP.

    Decyzja operatora jest faktem o operatorze, a budowa klienta faktem o poleceniu. Dopóki
    przypisanie stało w gałęzi `if online:`, `build_deps(asystent="wylaczony", online=False)`
    gubiło je bez śladu, a pierwszy ekran wracał do opisywania magazynu haseł. Żadne polecenie
    nie łączy dziś tych dwóch argumentów — to jest domknięcie stanu, nie naprawa usterki.

    Ustawienia budowane są **w całkowitej izolacji** (`environ={}`, `env_file=None`,
    `use_keyring=False`, własny `data_dir`). Pierwsza wersja wołała `load_settings` bez tego
    i przechodziła wyłącznie tam, gdzie w katalogu roboczym leżał `.env` z prawdziwym tokenem —
    czyli u autora, a nigdzie indziej. Wyszło to dopiero przy przenoszeniu pracy do monorepo.
    """
    settings = config.load_settings(
        token=TOKEN,
        environment="test",
        prod_consent=False,
        data_dir=tmp_path / "dane",
        environ={},
        env_file=None,
        use_keyring=False,
    )
    deps = build_deps(settings, online=False, asystent="wylaczony")

    assert deps.assistant_brak is not None
    assert deps.assistant_brak.powod == "WYLACZONY_FLAGA"
    deps.store.close()
