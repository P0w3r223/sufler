"""Ekran powitalny niesie obie granice, a nie tylko powitanie.

To jest przedmiot odbioru kroku 1: operator ma zobaczyć, kogo narzędzie obsługuje i że nie
łączy się z rejestrem, zanim o cokolwiek zapyta. Test stoi na modelu widoku, nie na
terminalu — po to `texts.py` jest modułem czystym.
"""

from __future__ import annotations

from typer.testing import CliRunner

from krs_tool.cli import app
from krs_tool.texts import pierwszy_ekran


def test_ekran_nazywa_granice_zakresu() -> None:
    tresc = pierwszy_ekran().as_text()

    assert "KRS" in tresc
    assert "jednoosobowe działalności" in tresc
    assert "ceidg-tool" in tresc


def test_ekran_mowi_ze_narzedzie_sie_nie_laczy() -> None:
    tresc = pierwszy_ekran().as_text()

    assert "nie łączy się z rejestrem" in tresc
    assert "z odpisu zapisanego wcześniej przez operatora" in tresc


def test_ekran_nie_obiecuje_oceny_terminowosci() -> None:
    """Reguła granic 11 od strony treści: etap 1 nie ocenia terminowości i mówi o tym."""
    tresc = pierwszy_ekran().as_text()

    assert "nie ocenia jeszcze" in tresc


def test_uruchomienie_bez_polecenia_pokazuje_ekran() -> None:
    wynik = CliRunner().invoke(app, [])

    assert wynik.exit_code == 0
    assert "krs-tool" in wynik.output
