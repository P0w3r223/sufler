"""Flagi, w których `x or domyślne` gubiło świadomą odpowiedź operatora (audyt A2, A6).

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


def test_lista_i_brak_flagi_znacza_dzis_to_samo() -> None:
    """A6 zniknęło razem ze swoim przedmiotem — i to jest jedyny ślad, jaki po nim zostaje.

    Trzy testy stały tu do 2026-09-10 i pilnowały, żeby `--lista` umiało powiedzieć „nie"
    plikowi zapytania, który mówił „tak": `False` było nieodróżnialne od „nie podano", więc
    `pobierz -z plik.yaml --lista --tak` szedł drogą **droższą**, a tabela kosztów potwierdzała
    gałąź, której operator przed chwilą odmówił.

    Po wycofaniu pliku (ADR-0022) nie ma wartości spod spodu, którą flaga miałaby wyłączać —
    kryteria powstają wyłącznie z flag, więc „operator nie powiedział nic" i „operator
    powiedział nie" prowadzą do tego samego, poprawnego wyniku. Test zostaje jako **zapis
    tej zmiany**: gdyby kiedyś wróciło jakiekolwiek źródło kryteriów spod spodu (plik,
    profil, zapamiętane zapytanie), ta asercja przestanie być prawdziwa i wtedy trzeba
    będzie przywrócić trójstanowość, a nie ją odtwarzać od zera po kolejnym audycie.
    """
    bez_flagi = _criteria_from_options({"wojewodztwo": ["podlaskie"]})
    z_lista = _criteria_from_options({"wojewodztwo": ["podlaskie"]}, szczegoly=False)
    ze_szczegolami = _criteria_from_options({"wojewodztwo": ["podlaskie"]}, szczegoly=True)

    assert bez_flagi.szczegoly is False
    assert z_lista.szczegoly is False
    assert ze_szczegolami.szczegoly is True
