"""Dwie flagi, w których `x or domyślne` gubiło świadomą odpowiedź operatora (audyt A2, A6).

Obie miały ten sam kształt: wartość podana przez operatora była **fałszywa w sensie Pythona**
(`0`, `False`), więc `or` odrzucał ją tak samo jak brak flagi. Obie nie miały ani jednego testu —
`grep -rn "starsze-niz" tests/` nie zwracał nic — i obie kosztowały coś, czego operator nie widzi:
jedna zostawiała dane osobowe, druga kupowała droższą gałąź pobierania.

Poprawny idiom stał w tym samym pliku, kilkanaście linijek dalej (`if maks is not None`).

`wyczysc` **nie jest tu uruchamiane**. To polecenie kasuje dane osobowe, a decyzja, którą trzeba
sprawdzić, jest czystą funkcją dwóch liczb — więc `dni_retencji` istnieje osobno właśnie po to,
żeby dało się ją sprawdzić bez wołania komendy.
"""

from __future__ import annotations

from pathlib import Path

from ceidg_tool.cli import _criteria_from_options, dni_retencji

DOMYSLNA_RETENCJA = 30


def test_zero_dni_znaczy_usun_wszystko_a_nie_uzyj_domyslnych() -> None:
    """Rdzeń A2. `--starsze-niz 0` to prośba o skasowanie całości, nie brak odpowiedzi.

    Przy `starsze_niz or domyslne` zero zamieniało się w 30 dni: operator prosił o usunięcie
    danych osobowych, dostawał komunikat o powodzeniu i zostawał z miesiącem wpisów.
    """
    assert dni_retencji(0, DOMYSLNA_RETENCJA) == 0


def test_brak_flagi_bierze_retencje_z_konfiguracji() -> None:
    """Kontrola pozytywna: gdyby funkcja zawsze zwracała argument, test wyżej byłby pusty."""
    assert dni_retencji(None, DOMYSLNA_RETENCJA) == DOMYSLNA_RETENCJA


def test_podana_liczba_dni_ma_pierwszenstwo_nad_konfiguracja() -> None:
    """Zwykły przypadek — tu `or` też działał, więc sam nie odróżniłby naprawy od defektu."""
    assert dni_retencji(7, DOMYSLNA_RETENCJA) == 7


def _z_pliku(tmp_path: Path, tresc: str, *, szczegoly: bool | None):  # type: ignore[no-untyped-def]
    plik = tmp_path / "zapytanie.yaml"
    plik.write_text(tresc, encoding="utf-8")
    return _criteria_from_options(
        plik, [], [], [], [], [], [], [], [], [], None, None, szczegoly, None
    )


def test_lista_wylacza_szczegoly_wlaczone_w_pliku(tmp_path: Path) -> None:
    """Rdzeń A6. `--lista` ma umieć powiedzieć „nie" plikowi, który mówi „tak".

    `pobierz -z plik.yaml --lista --tak` szedł drogą **droższą**: `False` było nieodróżnialne
    od „nie podano", więc nadpisanie nie zachodziło. Tabela kosztów potwierdzała potem tę
    gałąź, której operator właśnie odmówił, bo `flow` czyta wartość, która przeżyła.
    """
    kryteria = _z_pliku(tmp_path, "wojewodztwo: podlaskie\nszczegoly: true\n", szczegoly=False)

    assert kryteria.szczegoly is False


def test_szczegoly_wlaczaja_szczegoly_wylaczone_w_pliku(tmp_path: Path) -> None:
    """Druga strona tej samej trójstanowości — flaga wygrywa z plikiem w obie strony."""
    kryteria = _z_pliku(tmp_path, "wojewodztwo: podlaskie\nszczegoly: false\n", szczegoly=True)

    assert kryteria.szczegoly is True


def test_brak_flagi_zostawia_wartosc_z_pliku(tmp_path: Path) -> None:
    """Trzeci stan, i powód, dla którego `bool` tu nie wystarcza.

    Bez tego przypadku wystarczyłaby flaga dwustanowa: to on wymusza rozróżnienie
    „operator nie powiedział nic" od „operator powiedział nie"."""
    kryteria = _z_pliku(tmp_path, "wojewodztwo: podlaskie\nszczegoly: true\n", szczegoly=None)

    assert kryteria.szczegoly is True
