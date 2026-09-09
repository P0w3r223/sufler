"""Warstwa CLI — zgoda na produkcję i szczelność tokenu. Offline: żaden test nie tworzy klienta.

Produkcja to jedyne miejsce, gdzie narzędzie dotyka prawdziwych danych osobowych
(UZUPELNIENIE_01 §B). Zgoda jest argumentem, nie flagą globalną, więc każda droga do
`--srodowisko prod` musi kończyć się odmową, zanim powstanie klient HTTP i baza.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer import rich_utils
from typer.testing import CliRunner

import ceidg_tool.config as config
from ceidg_tool import cli
from ceidg_tool.cli import app
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
