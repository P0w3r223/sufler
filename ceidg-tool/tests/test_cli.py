"""Warstwa CLI — zgoda na produkcję i szczelność tokenu. Offline: żaden test nie tworzy klienta.

Produkcja to jedyne miejsce, gdzie narzędzie dotyka prawdziwych danych osobowych
(UZUPELNIENIE_01 §B). Zgoda jest argumentem, nie flagą globalną, więc każda droga do
`--srodowisko prod` musi kończyć się odmową, zanim powstanie klient HTTP i baza.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from typer import rich_utils
from typer.testing import CliRunner

import ceidg_tool.config as config
from ceidg_tool import cli
from ceidg_tool.cli import app
from ceidg_tool.criteria import Criteria
from ceidg_tool.errors import ProdWithoutConsentError

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
